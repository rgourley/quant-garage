# Rendering: smart-money-cluster

Composite note. Layout:

1. Header (1 line)
2. Per-fund summary block
3. Three cluster tables (initiations, adds, exits)
4. Take + caveats

Cluster tables sort by (n_funds desc, total_dollars desc). Cap at 15
entries with overflow count. Fund name lists truncate at 5 with `+N`.

Format: `Nx  {issuer_name:<38} {dollars:>10}   {funds joined by comma}`
