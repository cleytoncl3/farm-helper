---
name: farm-routine
description: "User's required order of actions when operating their o jogo farm in Chrome, plus hard limits (no selling, no $token spending)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 41e5c663-7a1b-4bca-a4cf-55bb0928d8ef
  modified: 2026-11-02T18:24:00.119Z
---

When running the user's game farm (address in game_url.txt), follow this order every cycle:
1. Feed Factory (building with blue jars): collect ready feed and queue cow/chicken feed again — BEFORE collecting milk/eggs.
2. Collect milk and feed the cow; collect eggs and feed the chickens.
3. Mining Station (grey rock): collect run and start a new run.
4. Beehive: collect honey if ready — click the beehive house itself (not the floating jar).
5. Trees: click the very bottom of the trunk (first pixels where the tree starts, on its own tile) of every tree — clicks on the canopy miss, front rows first then the ones behind, until only stumps remain on the grass; verify none left standing and ready.
6. Only then do crop rotation (harvest, rebuy same seeds, replant same layout, water in 3x3).

Farm is a 17x17 tile grid; always click the exact tile under an item (hover first to confirm).

**Why:** user stated this order explicitly; they were unhappy when steps were skipped or clicks landed on wrong tiles.
**How to apply:** do not ask whether to continue — just keep going. Never sell crops/items (user forbade it after I sold 90 pumpkins). The energy-pack token is a TESTNET faucet token; user asked me to buy energy with it — buying the best-value energy pack when energy runs out is OK. Still don't trade on the Farmers' Market or spend token on anything else without being asked. Space every action by a random 1–2 s.
