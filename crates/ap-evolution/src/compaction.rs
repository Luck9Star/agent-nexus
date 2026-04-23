//! Context compaction — simple truncation with ellipsis.
//!
//! In production this would call an LLM to summarise; for now we do a
//! deterministic truncation to keep the behaviour testable and free.

/// Truncate `context` to at most `max_tokens` tokens (approximated by bytes).
///
/// A rough 4-bytes-per-token estimate is used. If the context fits within the
/// budget it is returned unchanged; otherwise it is truncated and `...` is
/// appended.
#[must_use] 
pub fn compact(context: &str, max_tokens: usize) -> String {
    let max_bytes = max_tokens.saturating_mul(4);
    if context.len() <= max_bytes {
        return context.to_string();
    }

    // Leave room for the ellipsis
    let truncation_point = max_bytes.saturating_sub(3);
    // Floor to the nearest valid UTF-8 char boundary to avoid panic on multi-byte characters
    let safe_point = context.floor_char_boundary(truncation_point);
    let mut truncated = context[..safe_point].to_string();
    truncated.push_str("...");
    truncated
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn short_context_unchanged() {
        let ctx = "Hello, world!";
        let result = compact(ctx, 100);
        assert_eq!(result, ctx);
    }

    #[test]
    fn long_context_truncated() {
        let ctx = "A".repeat(1000);
        let result = compact(&ctx, 10); // 10 tokens * 4 bytes = 40 bytes max
        assert!(result.len() <= 40); // 10 tokens * 4 bytes = 40 max
        assert!(result.ends_with("..."));
    }

    #[test]
    fn exact_fit_not_truncated() {
        let ctx = "A".repeat(40);
        let result = compact(&ctx, 10); // 10 * 4 = 40, exact fit
        assert_eq!(result, ctx);
        assert!(!result.ends_with("..."));
    }

    #[test]
    fn zero_tokens_returns_ellipsis() {
        let ctx = "Some content";
        let result = compact(ctx, 0);
        assert_eq!(result, "...");
    }

    #[test]
    fn empty_context_returns_empty() {
        let result = compact("", 100);
        assert!(result.is_empty());
    }

    #[test]
    fn preserves_unicode_boundary_safely() {
        let ctx = "Hello, 世界! ".repeat(100);
        // 5 tokens * 4 = 20 bytes — will truncate, but should not panic
        let result = compact(&ctx, 5);
        assert!(result.ends_with("..."));
    }
}
