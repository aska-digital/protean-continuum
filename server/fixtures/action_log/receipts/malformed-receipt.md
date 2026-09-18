# fixture malformed receipt

This fixture deliberately carries nothing that maps onto an action keyword, and it
carries no separator glyph either. It exists so the parser fallback path and its
defined warnings can be exercised rather than assumed.
