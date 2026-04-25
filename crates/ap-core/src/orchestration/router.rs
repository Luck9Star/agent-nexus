//! `PlatformRouter`: 4-phase composite agent orchestration.
//!
//! Routes tasks to either atomic agents (single call) or composite agents
//! (4-phase workflow: research → synthesis → implementation → verification).
//!
//! Python source: `src/agent_nexus/platform/router/router.py` (~300 lines)

use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;

use crate::orchestration::ipc::IpcError;
use crate::orchestration::ipc_protocol::{AgentResult, IpcProtocol};
use crate::orchestration::process_manager::ProcessManagerHandle;
use crate::orchestration::subtask::{FactoryFn, SubtaskConfig, SubtaskController, SubtaskError};
use crate::orchestration::workflow::{CompositeWorkflowResult, PhaseResult, WorkflowContext, WorkflowPhase};

/// Error type for router operations.
#[derive(Debug, thiserror::Error)]
pub enum RouterError {
    #[error("Agent '{0}' is not registered")]
    AgentNotFound(String),
    #[error("IPC error: {0}")]
    Ipc(#[from] IpcError),
    #[error("Subtask error: {0}")]
    Subtask(#[from] SubtaskError),
    #[error("Process error: {0}")]
    Process(String),
    #[error("Phase '{phase}' failed: {error}")]
    PhaseFailed { phase: WorkflowPhase, error: String },
    #[error("No agents configured for phase '{0}'")]
    NoAgentsForPhase(WorkflowPhase),
}

/// Definition of a composite agent's sub-agents and phase assignments.
#[derive(Debug, Clone)]
pub struct CompositeDefinition {
    /// Agent names assigned to each phase.
    pub phase_agents: HashMap<WorkflowPhase, Vec<String>>,
}

/// The Platform Router orchestrates composite agent workflows.
///
/// For atomic agents, it delegates directly via IPC.
/// For composite agents, it runs the 4-phase workflow.
pub struct PlatformRouter {
    pm: ProcessManagerHandle,
    subtask: SubtaskController,
    composites: HashMap<String, CompositeDefinition>,
}

impl PlatformRouter {
    pub fn new(pm: ProcessManagerHandle, config: SubtaskConfig) -> Self {
        Self {
            pm,
            subtask: SubtaskController::new(config),
            composites: HashMap::new(),
        }
    }

    /// Register a composite agent definition.
    pub fn register_composite(&mut self, name: String, def: CompositeDefinition) {
        self.composites.insert(name, def);
    }

    /// Route a chat message to the appropriate agent.
    ///
    /// For composite agents, runs the full 4-phase workflow.
    /// For atomic agents, delegates via IPC.
    pub async fn route_chat(
        &self,
        agent_name: &str,
        message: &str,
        conversation_id: &str,
    ) -> Result<CompositeWorkflowResult, RouterError> {
        if let Some(def) = self.composites.get(agent_name) {
            self.route_composite(agent_name, def, message, conversation_id)
                .await
        } else {
            self.route_to_atomic(agent_name, message, conversation_id)
                .await
        }
    }

    /// Execute a 4-phase composite agent workflow.
    async fn route_composite(
        &self,
        agent_name: &str,
        def: &CompositeDefinition,
        message: &str,
        conversation_id: &str,
    ) -> Result<CompositeWorkflowResult, RouterError> {
        let mut ctx = WorkflowContext::new(
            conversation_id.to_string(),
            message.to_string(),
            agent_name.to_string(),
        );
        let total = WorkflowPhase::ordered().len() as u32;
        let mut completed = 0u32;

        // Overall timeout: phases × per-phase timeout. Prevents unbounded hangs.
        let composite_timeout = std::time::Duration::from_secs(
            self.subtask.config().timeout_seconds * total as u64,
        );

        let result = tokio::time::timeout(composite_timeout, async {
            let inner: Result<CompositeWorkflowResult, RouterError> = async {
                for phase in WorkflowPhase::ordered() {
                ctx.current_phase = Some(phase);
                let agent_names = def.phase_agents.get(&phase).cloned().unwrap_or_default();
                if agent_names.is_empty() {
                    // No agents for this phase — skip gracefully.
                    completed += 1;
                    continue;
                }

                let result = if phase.is_parallel() && agent_names.len() > 1 {
                    self.execute_parallel_agents(&agent_names, &ctx.phase_context, &ctx.conversation_id)
                        .await?
                } else {
                    let agent = &agent_names[0];
                    let agent_result = self
                        .ipc_chat(agent, &ctx.phase_context, &ctx.conversation_id)
                        .await
                        .map_err(|e| RouterError::PhaseFailed {
                            phase,
                            error: e.to_string(),
                        })?;
                    vec![agent_result]
                };

                let output = Self::aggregate_results(&result);
                let success = result.iter().all(|r| r.success);

                ctx.phase_results.insert(
                    phase,
                    PhaseResult {
                        phase,
                        success,
                        output: output.clone(),
                        error: if success {
                            None
                        } else {
                            Some("One or more agents reported failure".into())
                        },
                    },
                );

                if !success {
                    return Ok(CompositeWorkflowResult {
                        success: false,
                        final_output: output,
                        phase_results: ctx.phase_results,
                        total_phases: total,
                        completed_phases: completed,
                        error: Some(format!("Phase '{phase}' failed")),
                        error_type: Some("phase_failure".into()),
                    });
                }

                // Enrich context for the next phase with heading + instruction.
                ctx.phase_context = Self::build_phase_message(phase, &output);
                completed += 1;
            }

            Ok(CompositeWorkflowResult {
                success: true,
                final_output: ctx.phase_context,
                phase_results: ctx.phase_results,
                total_phases: total,
                completed_phases: completed,
                error: None,
                error_type: None,
            })
            }
            .await;
            inner
        })
        .await
        .map_err(|_| {
            // Timeout elapsed
            RouterError::PhaseFailed {
                phase: ctx.current_phase.unwrap_or(WorkflowPhase::Research),
                error: format!(
                    "Composite workflow timed out after {}s",
                    composite_timeout.as_secs()
                ),
            }
        })??;

        Ok(result)
    }

    /// Build the message for the next phase based on completed results.
    ///
    /// Each phase output is wrapped with a heading and an instruction for the
    /// next phase, providing context enrichment between phases. Matches Python's
    /// `_PHASE_MESSAGE_TEMPLATES` behavior.
    fn build_phase_message(phase: WorkflowPhase, phase_result: &str) -> String {
        match phase {
            WorkflowPhase::Research => {
                format!(
                    "## Research Results\n\n{}\n\nBased on the above research, create an implementation plan.",
                    phase_result
                )
            }
            WorkflowPhase::Synthesis => {
                format!(
                    "## Implementation Plan\n\n{}\n\nExecute the above plan.",
                    phase_result
                )
            }
            WorkflowPhase::Implementation => {
                format!(
                    "## Implementation Output\n\n{}\n\nVerify the above implementation is correct and complete.",
                    phase_result
                )
            }
            WorkflowPhase::Verification => phase_result.to_string(),
        }
    }

    /// Route to a single atomic agent via IPC.
    async fn route_to_atomic(
        &self,
        agent_name: &str,
        message: &str,
        conversation_id: &str,
    ) -> Result<CompositeWorkflowResult, RouterError> {
        let agent_result = self
            .ipc_chat(agent_name, message, conversation_id)
            .await
            .map_err(|e| RouterError::Process(e.to_string()))?;

        let success = agent_result.success;
        let output = agent_result.content.clone();

        let mut phase_results = HashMap::new();
        phase_results.insert(
            WorkflowPhase::Implementation,
            PhaseResult {
                phase: WorkflowPhase::Implementation,
                success,
                output: output.clone(),
                error: if success { None } else { Some("Agent reported failure".into()) },
            },
        );

        Ok(CompositeWorkflowResult {
            success,
            final_output: output,
            phase_results,
            total_phases: 1,
            completed_phases: 1,
            error: if success { None } else { Some("Agent execution failed".into()) },
            error_type: if success { None } else { Some("agent_failure".into()) },
        })
    }

    /// Execute multiple agents in parallel with bounded concurrency.
    async fn execute_parallel_agents(
        &self,
        agent_names: &[String],
        message: &str,
        conversation_id: &str,
    ) -> Result<Vec<AgentResult>, RouterError> {
        // Deduplicate agent names
        let unique: Vec<String> = {
            let mut seen = std::collections::HashSet::new();
            agent_names
                .iter()
                .filter(|n| seen.insert((*n).clone()))
                .cloned()
                .collect()
        };

        let timeout_secs = self.subtask.config().timeout_seconds as f64;
        let pm = self.pm.clone(); // Arc clone — cheap, each factory gets its own Arc

        let factories: Vec<FactoryFn<AgentResult>> = unique
            .iter()
            .map(|name| {
                let name = name.clone();
                let msg = message.to_string();
                let conv_id = conversation_id.to_string();
                let pm = pm.clone();
                let timeout = timeout_secs;

                Box::new(move || {
                    // Clone captured values for the async block (Fn, not FnOnce)
                    let name = name.clone();
                    let msg = msg.clone();
                    let conv_id = conv_id.clone();
                    let pm = pm.clone();

                    Box::pin(async move {
                        // Take IO from ProcessManager (serialized via inner Mutex)
                        let (stdin, stdout) = pm.take_io(&name).await.map_err(|e| {
                            SubtaskError::Execution(format!("Failed to get IO for '{name}': {e}"))
                        })?;

                        let mut proto = IpcProtocol::new(stdout, stdin);
                        proto
                            .send_chat(&msg, Some(&conv_id))
                            .await
                            .map_err(|e| SubtaskError::Execution(format!("IPC send: {e}")))?;

                        let result = proto
                            .receive_result(Some(timeout))
                            .await
                            .map_err(|e| SubtaskError::Execution(format!("IPC recv: {e}")))?;

                        // Return IO so the agent can be reused in subsequent phases.
                        let (stdout_r, stdin_w) = proto.into_parts();
                        if let Err(e) = pm.return_io(&name, (stdin_w, stdout_r)).await {
                            tracing::warn!("Failed to return IO for agent '{name}': {e}");
                        }

                        Ok(result)
                    })
                        as Pin<Box<dyn Future<Output = Result<AgentResult, SubtaskError>> + Send>>
                }) as FactoryFn<AgentResult>
            })
            .collect();

        let results = self.subtask.run_parallel(factories).await;
        // Convert SubtaskError results to AgentResults
        Ok(results
            .into_iter()
            .map(|r| match r {
                Ok(agent_result) => agent_result,
                Err(e) => AgentResult {
                    content: format!("Subtask error: {e}"),
                    success: false,
                    task_id: None,
                },
            })
            .collect())
    }

    /// Send a chat message to an agent via IPC and receive the result.
    ///
    /// Takes the IO pair from the ProcessManager, performs the chat, then
    /// returns the IO pair. The pair is NOT returned to the PM — the caller
    /// is responsible for managing process lifecycle.
    async fn ipc_chat(
        &self,
        agent_name: &str,
        message: &str,
        conversation_id: &str,
    ) -> Result<AgentResult, RouterError> {
        let (stdin, stdout) = self
            .pm
            .take_io(agent_name)
            .await
            .map_err(|e| RouterError::Process(format!("Failed to get IO for '{agent_name}': {e}")))?;

        let mut proto = IpcProtocol::new(stdout, stdin);
        proto
            .send_chat(message, Some(conversation_id))
            .await?;

        let timeout_secs = self.subtask.config().timeout_seconds as f64;
        let result = proto.receive_result(Some(timeout_secs)).await?;

        // Return IO to ProcessManager so the agent can be reused in subsequent phases.
        let (stdout_r, stdin_w) = proto.into_parts();
        if let Err(e) = self.pm.return_io(agent_name, (stdin_w, stdout_r)).await {
            tracing::warn!("Failed to return IO for agent '{agent_name}': {e}");
        }

        Ok(result)
    }

    /// Aggregate multiple agent results into a single string.
    pub(crate) fn aggregate_results(results: &[AgentResult]) -> String {
        if results.len() == 1 {
            return results[0].content.clone();
        }
        results
            .iter()
            .enumerate()
            .map(|(i, r)| format!("--- Agent {} ---\n{}", i + 1, r.content))
            .collect::<Vec<_>>()
            .join("\n\n")
    }

    /// Query all registered composite agents for their sub-agent names.
    ///
    /// Returns a map of composite name → list of sub-agent names.
    pub fn get_tools(&self) -> HashMap<String, Vec<String>> {
        let mut tools = HashMap::new();
        for (name, def) in &self.composites {
            let agents: Vec<String> = def
                .phase_agents
                .values()
                .flatten()
                .cloned()
                .collect();
            tools.insert(name.clone(), agents);
        }
        tools
    }

    /// Stop all managed agent processes gracefully.
    ///
    /// Attempts graceful shutdown (SIGTERM + timeout), then force kills remaining.
    pub async fn stop_all(&self) -> Result<(), RouterError> {
        let timeout = std::time::Duration::from_secs(self.subtask.config().timeout_seconds);
        if let Err(e) = self.pm.graceful_shutdown_all(timeout).await {
            tracing::warn!("Graceful shutdown failed ({}), force-killing all agents", e);
            self.pm.kill_all().await.map_err(|e| RouterError::Process(e.to_string()))?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use crate::models::agent::AgentRole;

    fn phase_role_map() -> HashMap<WorkflowPhase, AgentRole> {
        let mut m = HashMap::new();
        m.insert(WorkflowPhase::Research, AgentRole::Explore);
        m.insert(WorkflowPhase::Synthesis, AgentRole::Plan);
        m.insert(WorkflowPhase::Implementation, AgentRole::Worker);
        m.insert(WorkflowPhase::Verification, AgentRole::Verification);
        m
    }

    #[test]
    fn phase_role_map_covers_all_phases() {
        let map = phase_role_map();
        assert_eq!(map.len(), 4);
        assert_eq!(map[&WorkflowPhase::Research], AgentRole::Explore);
        assert_eq!(map[&WorkflowPhase::Synthesis], AgentRole::Plan);
        assert_eq!(map[&WorkflowPhase::Implementation], AgentRole::Worker);
        assert_eq!(map[&WorkflowPhase::Verification], AgentRole::Verification);
    }

    #[test]
    fn workflow_phase_ordering() {
        let ordered = WorkflowPhase::ordered();
        assert_eq!(ordered.len(), 4);
        assert_eq!(ordered[0], WorkflowPhase::Research);
        assert_eq!(ordered[3], WorkflowPhase::Verification);
    }

    #[test]
    fn aggregate_results_single() {
        let results = vec![AgentResult {
            content: "hello".into(),
            success: true,
            task_id: None,
        }];
        assert_eq!(PlatformRouter::aggregate_results(&results), "hello");
    }

    #[test]
    fn aggregate_results_multiple() {
        let results = vec![
            AgentResult {
                content: "first".into(),
                success: true,
                task_id: None,
            },
            AgentResult {
                content: "second".into(),
                success: true,
                task_id: None,
            },
        ];
        let aggregated = PlatformRouter::aggregate_results(&results);
        assert!(aggregated.contains("first"));
        assert!(aggregated.contains("second"));
        assert!(aggregated.contains("Agent 1"));
        assert!(aggregated.contains("Agent 2"));
    }

    // aggregate_results is now pub(crate) — tested directly below.
}
