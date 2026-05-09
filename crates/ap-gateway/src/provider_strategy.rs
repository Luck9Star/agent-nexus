//! Provider-aware tool loading strategy selection.
//!
//! The Gateway is the only place aware of the user's model provider (from config).
//! It selects tool loading strategies based on the provider string:
//! - Anthropic provider -> native deferred loading (zero round-trip)
//! - Other providers -> standard eager loading

/// Strategy for how tools are presented to the LLM.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToolLoadingStrategy {
    /// Standard: all tools loaded eagerly.
    Eager,
    /// Lazy: tools loaded on first use.
    #[allow(dead_code)] // Reserved for future lazy-loading support
    Lazy,
    /// Anthropic native: use tool_search_tool for deferred loading.
    AnthropicDeferred,
}

/// Selects tool loading strategy based on model provider string.
///
/// Model strings follow the format `provider:model_name` (e.g. `anthropic:claude-sonnet-4-20250514`).
/// Only `anthropic:` prefixed models use the deferred strategy; all others default to eager.
pub struct ProviderAwareStrategy;

impl ProviderAwareStrategy {
    /// Select the appropriate tool loading strategy for the given model string.
    ///
    /// # Arguments
    /// * `model` - Model string in `provider:model_name` format
    ///
    /// # Returns
    /// * `ToolLoadingStrategy::AnthropicDeferred` for `anthropic:*` models
    /// * `ToolLoadingStrategy::Eager` for all other providers (safe default)
    #[must_use]
    pub fn select_strategy(model: &str) -> ToolLoadingStrategy {
        if model.to_ascii_lowercase().starts_with("anthropic:") {
            ToolLoadingStrategy::AnthropicDeferred
        } else {
            ToolLoadingStrategy::Eager
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn anthropic_model_deferred() {
        assert_eq!(
            ProviderAwareStrategy::select_strategy("anthropic:claude-sonnet-4-20250514"),
            ToolLoadingStrategy::AnthropicDeferred
        );
    }

    #[test]
    fn openai_model_eager() {
        assert_eq!(
            ProviderAwareStrategy::select_strategy("openai:gpt-4o"),
            ToolLoadingStrategy::Eager
        );
    }

    #[test]
    fn ollama_model_eager() {
        assert_eq!(
            ProviderAwareStrategy::select_strategy("ollama:llama3"),
            ToolLoadingStrategy::Eager
        );
    }

    #[test]
    fn unknown_provider_eager() {
        assert_eq!(
            ProviderAwareStrategy::select_strategy("unknown:x"),
            ToolLoadingStrategy::Eager
        );
    }

    #[test]
    fn provider_case_insensitive() {
        assert_eq!(
            ProviderAwareStrategy::select_strategy("Anthropic:claude-3"),
            ToolLoadingStrategy::AnthropicDeferred
        );
        assert_eq!(
            ProviderAwareStrategy::select_strategy("ANTHROPIC:claude-3"),
            ToolLoadingStrategy::AnthropicDeferred
        );
    }
}
