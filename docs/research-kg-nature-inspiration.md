# Research: Knowledge Graphs, Neuroscience, and Nature-Inspired Design

**Date:** 2026-04-11
**Status:** Research synthesis, informing next design decisions

## The Question

Extended-thinking has a working unified graph (52 nodes, 60 edges, federated across ET + mempalace). But the graph is static, noisy, and the two subgraphs are disconnected. How should a knowledge graph behave? What can we learn from systems that have solved this problem at scale: brains, forests, slime molds, ant colonies?

## From the Brain (Connectome)

### Small-world topology
86 billion neurons, ~10^15 synapses, but less than 1% of possible connections form. High local clustering (53% vs 22% in random graphs) with short path lengths (2.5 average hops). Both local specialization and global integration, simultaneously.

*Design implication: a good concept graph has tight local clusters with a few long-range bridges. Not a flat mesh, not a tree.*

Sources: van den Heuvel & Sporns 2011; Bassett & Bullmore 2017.

### Rich-club hubs
12 bihemispheric hub regions (precuneus, superior frontal, thalamus, hippocampus, etc.) form a densely interconnected "rich club." 89% of all shortest paths between non-hub regions pass through at least one rich-club node. Removing rich-club connections drops global efficiency by 18%, 3x worse than random attacks.

*Design implication: let hubs emerge naturally, protect them, route through them. Hub nodes are the backbone for integrating functionally diverse subsystems.*

### Memory consolidation (sleep replay)
New memories encode in the hippocampus, then transfer to cortex during non-REM sleep. Mechanism: hippocampal sharp-wave ripples replay waking sequences 15-20x compressed, nested inside thalamocortical spindles, nested inside cortical slow oscillations. 2-4 replay events per second during non-REM.

*Design implication: `et_insight` is the sleep replay. Compressed re-processing of recent work, extracting what matters, routing it to long-term concept storage. The compression ratio matters: 15-20x, not 1:1.*

Sources: Buzsaki 2015; Rasch & Born, Physiol Rev 2024.

### Associative memory (cross-domain linking)
Apple to Newton to gravity to orbits: works through overlapping sparse neural ensembles. The hippocampus creates conjunctive codes binding disparate cortical representations. The anterior temporal lobe acts as a transmodal hub where visual, auditory, and linguistic features converge into amodal concept representations. Semantic distance maps to actual network distance.

*Design implication: cross-domain association needs a hub mechanism. Concepts from different providers (ET vs mempalace) need a "temporal lobe equivalent" that recognizes when they're about the same thing.*

Sources: Bhatt et al., PNAS 2025.

### Sparse coding
Sensory cortex: only a few percent of neurons active at any time. Lifetime sparseness ~0.88. Exponential distribution: most neurons near zero, a few fire strongly. A single image is decodable from a surprisingly small subset; the rest degrade decoding.

*Design implication: don't show everything. Show the few percent that are "firing" right now. The active set should be sparse and high-signal.*

Sources: Willmore et al. 2011.

### Default mode network (background recombination)
The DMN (medial prefrontal, posterior cingulate, angular gyrus, hippocampal formation) activates during mind-wandering. Not idle: running background recombination of stored concepts without executive filtering. Creativity correlates with the number of dynamic switches between DMN and executive control network across 2,433 participants (2025 study). Jazz musicians in flow states show transient hypofrontality.

*Design implication: the system needs a "DMN mode" that freely recombines concepts across domains for serendipity. Not retrieval, recombination.*

Sources: Beaty et al., Brain 2024; Comm Bio 2025.

### Hebbian learning and pruning
LTP via NMDA receptors: concurrent pre/post-synaptic activity strengthens connections. Reverse order weakens them (LTD). Microglia eliminate weak synapses tagged by complement proteins. The brain prunes ~50% of synapses between childhood and adulthood. Forgetting maintains signal-to-noise ratio.

*Design implication: forgetting is a feature. Edges not traversed should decay. Active pruning is maintenance, not data loss.*

Sources: Royal Society 2024; PMC 2025.

## From Nature

### Mycelial networks (Wood Wide Web)
Weighted undirected graphs with loops from hyphal fusion. Three transport scales: diffusion, motor-driven (1-4 um/s), growth-induced mass flow (20-100 mm/h). Critical property: tubes carrying more flow thicken (positive feedback reinforcing high-traffic routes). Damage resilience from loop topology. Hub trees ("mother trees") preferentially allocate resources to kin.

*Design implication: flow-responsive edges. Concepts traversed together should strengthen their connection automatically. Loop topology for resilience: ensure multiple paths between concepts.*

Sources: Simard, Frontiers 2024; PMC 2024.

### Physarum polycephalum (slime mold)
One rule: tubes with more flow thicken, tubes with less flow shrink.
```
dD/dt = f(|Q|) - rD
```
Where D = conductance, Q = flow, r = decay rate. This single feedback loop converges to near-optimal Steiner trees. Solved the Tokyo rail network. Applied to decentralized mesh networks (2025).

*Design implication: the same decay equation for edge weights. No manual pruning. The math is simple and proven.*

Sources: Tero et al.; Scientific Reports 2025.

### Ant colony stigmergy
Pheromone trails as distributed external memory. Positive feedback: trails used more get reinforced. Evaporation as automatic forgetting (differential decay rates prioritize recent information). Shortest paths emerge because shorter routes get more round-trips, more pheromone. No central coordinator.

*Design implication: every interaction with a concept is a trace. Access = pheromone deposit. Time = evaporation. Recent + frequent = strong. Old + unused = fades.*

Sources: Nature Comm Eng 2024; PMC 2024.

### Scale-free networks (Barabasi-Albert)
Power-law degree distributions from growth + preferential attachment. Resilient to random failure, fragile to targeted hub removal. Caveat: Broido & Clauset (2019) found strict scale-free structure is rare in real networks. Most are "broad-scale."

*Design implication: hubs form naturally. Monitor for hub fragility. Don't enforce strict power-law, let broad-scale emerge.*

### Metabolic bow-tie architecture
Thousands of inputs funnel through exactly 12 core precursor metabolites (glucose-6-phosphate, pyruvate, acetyl-CoA, etc.), then fan out to thousands of outputs. Core is conserved and redundantly connected. Periphery varies wildly. Fractal: nested bow-ties at every scale.

*Design implication: many raw inputs (chunks, sessions, notes) funnel through a few core concepts (recurring themes), then fan out to many outputs (wisdoms, actions). The core concepts are the value. Protect and monitor them.*

Sources: BMC Bioinformatics.

### Rhizomatic knowledge (Deleuze & Guattari)
Six principles: connection (any node to any node), heterogeneity (different types coexist), multiplicity (no unity to totalize), asignifying rupture (break anywhere, it regrows), cartography (map, don't trace), decalcomania (no deep structure to copy). Contrast with trees: a tree enforces one path root-to-leaf. A rhizome allows lateral movement across any nodes.

*Design implication: taxonomies are useful but should be embedded within a larger rhizomatic graph, not imposed as the governing structure. No single root, no mandatory hierarchy.*

## From the Research Frontier (2024-2026)

### The gap nobody fills
Everyone builds memory (MemPalace, Mem0, Zep, Letta). Nobody builds the meta-cognitive layer. CLARION's meta-cognitive subsystem (monitors, controls, regulates cognitive processes) is the closest academic analog to extended-thinking. CoALA (2023) is the reference framework for LLM agent cognition: working memory, episodic/semantic/procedural long-term memory, action loops.

### Temporal KGs
Graphiti/Zep: bi-temporal model with four timestamps (t_created, t_expired, t_valid, t_invalid). 94.8% DMR benchmark. P95 retrieval at 300ms. Our Fact dataclass already has valid_from/valid_to. Could adopt full bi-temporal.

### Link prediction
RotatE-family models score candidate edges between existing nodes. Practical path: embed the concept graph, rank all non-existent edges by score, surface top-k as suggestions. "You should connect A to B" becomes a computed recommendation.

### Graph-of-Thought persistence
AGoT (Feb 2025) builds dynamic DAGs at inference time, +46.2% on GPQA. Nobody persists these graphs. Capturing reasoning graphs across sessions and accumulating them would be unique.

### Serendipity
Academic consensus (RecSys 2024): serendipity = relevant + novel + unexpected. Weak ties (Granovetter) are the strongest predictor of useful surprise. Left to optimize naturally, systems converge on filter bubbles. Serendipity requires explicit architectural commitment.

### Concept extraction
ConExion (ESWC 2025) extracts domain concepts from text using LLMs, outperforming keyword methods. Abstract concepts require multi-turn context, not single-pass NER.

## Six Design Principles

These emerge from all three research angles:

| # | Principle | Source | Mechanism |
|---|-----------|--------|-----------|
| 1 | **Flow-responsive edges** | Mycelium, Physarum, Hebbian LTP | Edges used more get stronger. Weight += on traversal. |
| 2 | **Evaporative decay** | Ant pheromones, synaptic pruning | Edges/nodes not accessed decay over time. No manual cleanup. |
| 3 | **Rich-club hubs** | Brain connectome | Let hubs emerge naturally, protect them, route through them. |
| 4 | **Sparse active set** | Cortical sparse coding | Don't show everything. Show the few percent "firing" right now. |
| 5 | **Background recombination** | Default mode network | A mode that freely recombines concepts across domains. |
| 6 | **Bow-tie processing** | Metabolic networks | Many inputs, few core concepts, many outputs. Core is the value. |

The brain, mycelium, and slime mold all converge on the same pattern: **strengthen what flows, decay what doesn't, keep a few hubs, prune aggressively, and let the idle moments do the creative work.**

## Possible Implementation Leads

Ordered by impact and dependency. Each maps a research principle to a concrete code change.

### A. SAME_AS bridging (fixes disconnected subgraphs)
**Principle:** Associative memory, transmodal hub (anterior temporal lobe).
**Problem:** ET concepts and MP entities with the same name exist as separate nodes. The unified graph is two islands on one table.
**Mechanism:** When an ET concept and an MP entity share a name (or are semantically close), create a virtual SAME_AS edge. Simplest version: normalized string matching. Better version: LLM-judged similarity.
**Impact:** Immediately connects the two subgraphs. Cross-store traversal starts working.

### B. Edge weight decay + reinforcement (makes the graph alive)
**Principle:** Physarum conductance equation, Hebbian LTP/LTD, ant pheromone evaporation.
**Problem:** Edges are frozen at creation weight forever. No connection strengthens with use or fades with neglect.
**Mechanism:** Add `last_accessed` and `access_count` to edges. Every traversal (et_explore, et_path) increments both. Decay function: `weight *= decay_rate ^ days_since_access`. The Physarum equation, simplified.
**Impact:** The graph self-organizes over time. Frequently used paths become highways, unused ones fade.

### C. Sparse active set (reduces noise, shows what matters)
**Principle:** Cortical sparse coding (few percent active, exponential distribution).
**Problem:** All 52 nodes shown equally. Wisdom IDs, long bug names, core concepts all same visual weight.
**Mechanism:** `active_nodes(k)` returns top-k nodes by combined score of frequency, recency, and connectivity. The "firing" set. Everything else dimmed or hidden in graph view.
**Impact:** Graph becomes readable. Shows the signal, not the noise.

### D. Background recombination (serendipity engine)
**Principle:** Default mode network, weak ties (Granovetter), serendipity = relevant + novel + unexpected.
**Problem:** Graph only shows existing connections. Never surfaces unexpected cross-domain links.
**Mechanism:** Pick two random concepts from different clusters. Ask LLM: "is there a meaningful connection?" If yes, suggest as a new edge. Runs during `et_insight`, not retrieval but creative recombination.
**Impact:** The differentiator. The thing nobody else does. Serendipity by architecture, not accident.

### E. Bow-tie core identification (surfaces recurring themes)
**Principle:** Metabolic bow-tie (12 core metabolites, many inputs, many outputs).
**Problem:** No distinction between core recurring themes and peripheral one-off concepts.
**Mechanism:** Identify concepts that sit at the convergence point: high in-degree from raw inputs, high out-degree to wisdoms/actions. Surface explicitly in `et_graph` as "your core themes."
**Impact:** User sees what they actually think about most, separated from noise.

### Dependencies
A is standalone and highest impact. B and C are independent of each other but both benefit from A being done first (more edges to decay/rank). D requires enough graph density to be meaningful (do after A). E is analysis on the existing graph, can be done anytime.

### F. Data reset + content filtering (post-plan TODO)
**Problem:** Current data is 10 concepts extracted from mempalace's code-chunked drawers. Code is ephemeral, not thinking. The 60k drawers are .py, .toml, Cargo.toml files. The 3 wisdoms are artifacts of running the pipeline on wrong input.
**Action:**
1. Wipe concepts.db and vectors/ (fresh start)
2. Configure sync to only ingest: .md files, comments, conversation transcripts (Claude Code sessions), diary entries
3. Filter out source code from provider chunks before extraction
4. Re-run sync against meaningful content only
**When:** After the current storage layer plan (Phases 0-5) is complete.
