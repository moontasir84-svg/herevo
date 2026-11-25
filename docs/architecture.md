# HerEvo architecture

```mermaid
flowchart LR
  subgraph Channels
    TG[Telegram<br/>text + voice notes]
    WEB[Web app]
    SMS[SMS / phone call<br/>planned]
    OFF[Offline app<br/>planned]
  end

  subgraph API[HerEvo API · FastAPI]
    STT[Speech to text<br/>Google, bn-BD]
    SAFE{Safety screen<br/>rules, before any AI}
    RET[Hybrid retrieval<br/>pgvector + keyword · RRF]
    LLM[Claude<br/>one call · forced tool output]
    VAL{Validate<br/>Pydantic}
    FB[Fallback<br/>rules + pre-checked text]
    REC[Records]
  end

  subgraph Data[PostgreSQL + pgvector]
    PACKS[(Country packs<br/>BD · KE · MX)]
    SALES[(Income records<br/>consent-based)]
    CASES[(Anonymized cases)]
    TURNS[(Audit log)]
  end

  HELPER[Trained human helper<br/>Telegram /reply]
  MCP[MCP server<br/>herevo-country-packs]
  PARTNERS[Partner AI assistants]
  MAP[Evidence map<br/>/v1/evidence]

  TG --> STT --> SAFE
  WEB --> SAFE
  SAFE -- threat --> HELPER
  SAFE -- ok --> RET --> LLM --> VAL
  VAL -- invalid / AI down --> FB
  VAL -- ok --> REC
  FB --> REC
  RET <--> PACKS
  REC --> SALES & CASES & TURNS
  CASES --> MAP
  PACKS -. same YAML files .- MCP --> PARTNERS
```

## Why it is built this way

- **Fixed pipeline, not an agent.** Users are vulnerable; every step must be predictable and auditable.
  The order never changes and safety always runs first.
- **Country packs as data.** Laws, contacts and advice change; local partners update a YAML file and
  redeploy, no retraining. A new country is a new pack.
- **Hybrid retrieval.** Keyword search catches the exact local words women use; vectors catch meaning.
  Bangla gets light suffix stripping and prefix matching so "ধানের" (of rice) finds "ধান" (rice).
- **One structured Claude call.** Routing, safety double-check, grounded answer, English translation and
  sale extraction in one forced tool call: cheaper, faster, always machine-checkable.
- **Graceful degradation.** No key, a network error or invalid output never breaks a conversation:
  the system falls back to rules and pre-checked text.
- **Privacy by design.** Salted user hashes, no phone numbers, anonymized evidence with small cells
  hidden, message text purged after 30 days, `/delete` for the right to be forgotten.
