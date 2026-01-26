```
flow_lens/
│
├─ app/
│  ├─ main.py              # entrypoint, bootstraps engine + tui
│  ├─ config.py            # loads app.toml
│  │
│  ├─ engine/
│  │  ├─ state_engine.py   # X, Y, normalization, smoothing
│  │  ├─ buffer.py         # rolling event buffer
│  │  ├─ aggregation.py    # E_spot, E_perp, per-source effort
│  │  ├─ dispersion.py     # halo computation
│  │  └─ constants.py      # FL-0014 defaults
│  │
│  ├─ adapters/
│  │  ├─ base.py           # adapter interface
│  │  ├─ binance_spot_ws.py
│  │  └─ binance_perp_ws.py
│  │
│  ├─ models/
│  │  ├─ event.py          # effort event record
│  │  └─ flow_frame.py     # standardized frame to engine
│  │
│  └─ tui/
│     ├─ renderer.py       # dot, halo, lean drawing
│     └─ input.py          # symbol switching + slash search
│
├─ config/
│  └─ app.toml
│
├─ decisions/              # FL-XXXX docs
├─ storyboards/            # trap, squeeze, continuation, air pocket
├─ AGENTS.md
└─ README.md
```