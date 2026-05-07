//! CLI Backend Integration — config-driven CLI agent backend for LLM calls.

pub mod archive;
pub mod backend;
pub mod health;
pub mod parser;
pub mod registry;
pub mod router;
pub mod session;
pub mod setup;
pub mod types;

pub use setup::{call_llm, CLISetup};
