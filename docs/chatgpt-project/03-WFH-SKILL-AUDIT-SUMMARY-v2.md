# WaterfallHunter Skill Audit v2 — Project Source Summary

Council v2 keeps the thirteen domain skills and adds one meta-skill: `skill-system-curator`. No existing domain skill is merged or removed because their semantic ownership remains distinct.

Key improvements:

- explicit Input, Required Evidence, Tool Preference, Output, and Stop/Escalation contracts on every canonical skill;
- a self-audit owner for trigger overlap, contradictory instructions, stale tool assumptions, handoff loops, adapter drift, and behavioral coverage;
- explicit capability authorization states rather than assuming a plugin/MCP is usable because it is installed;
- deterministic Council v2 routes and exclusive production authority for the release certifier;
- exact-Git-object validation hooks and pressure-test coverage;
- a lightweight Project Sources overlay that points to canonical GitHub skill bodies instead of copying them into Drive.

External authoring guidance reviewed for v2 emphasizes progressive disclosure, concise triggerable skills, deterministic scripts for checks that agents might otherwise guess, and eval/pressure-test iteration. MCP guidance reviewed for v2 emphasizes explicit capabilities and hardened authorization. These external sources inform skill-system structure only; they do not replace WaterfallHunter repository/runtime facts.

Council v2 route reachability is validated: `capability_scout` participates in `skill_system_audit`, and `research_librarian` participates in the dedicated `external_research` route.
