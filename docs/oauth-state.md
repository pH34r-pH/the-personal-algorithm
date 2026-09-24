# Durable OAuth state

Hosted authorization state is stored in the application's durable SQLite state rather than process memory.

## Properties

- 256-bit-class URL-safe random state token;
- only SHA-256 digest persisted;
- HTTPS callback URI required;
- ten-minute default lifetime;
- exactly-once consumption;
- transaction begins with `BEGIN IMMEDIATE` so competing callback requests cannot both consume the same state;
- consumed/expired records can be pruned;
- state survives application process restarts when SQLite storage is persistent.

The raw state token exists only in the browser authorization round trip.

## Scaling boundary

SQLite is sufficient for the initial single-instance deployment. If the application is later scaled to multiple replicas that do not share the same SQLite file, OAuth state must move to a shared transactional backend before horizontal scaling is enabled.

Do not weaken one-time consumption to accommodate multiple replicas.
