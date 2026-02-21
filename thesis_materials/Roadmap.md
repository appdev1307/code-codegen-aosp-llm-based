
------------------------------------ Full Pipeline With All Agents: 
VSS signals
    ↓
VSSLabellingAgent          ← Fix 1 (retry) already done
    ↓
[RAG: retrieve AOSP context per signal type]
    ↓
ModulePlannerAgent         ← DSPy optimizes grouping prompt
    ↓
ArchitectAgent             ← RAG (AIDL examples) + DSPy
  ├── AIDL Agent           ← RAG (.aidl files) + DSPy
  ├── VHAL C++ Agent       ← RAG (.cpp/.h files) + DSPy
  ├── SELinux Agent        ← RAG (.te policy files) + DSPy
  └── Build Agent          ← RAG (Android.bp files) + DSPy
    ↓
DesignDocAgent             ← RAG (existing AOSP docs) + DSPy
    ↓
AndroidAppAgent            ← RAG (Car API examples) + DSPy
    ↓
BackendAgent               ← RAG (FastAPI/Python patterns) + DSPy
    ↓
BuildGlueAgent             ← RAG (VINTF/manifest files) + DSPy
------------------------------------

------------------------------------ RAG Sources Per Agent
Agent                  RAG Source Files              Query Built From
─────────────────────────────────────────────────────────────────────
VSSLabelling           VSS spec docs, signal lists   signal path + type
ModulePlanner          AOSP HAL domain docs           signal domain names
ArchitectAgent         Full HAL module examples       domain + property types
AIDL Agent             *.aidl files                   property names + types
VHAL C++ Agent         *.cpp, *.h VHAL files          domain + property types
SELinux Agent          *.te, file_contexts files      domain + service name
Build Agent            Android.bp files               module name + deps
DesignDoc Agent        AOSP design docs, READMEs      domain description
AndroidApp Agent       CarPropertyManager examples    signal names + types
                       Car API Java/Kotlin files
Backend Agent          FastAPI examples               property types + access
                       OpenAPI spec examples
BuildGlue Agent        manifest.xml, init.rc          domain + HAL version
                       VINTF fragments
------------------------------------

## Build Order (sequence matters)

```
Step 1 — Setup
  pip install chromadb sentence-transformers dspy-ai

Step 2 — Download AOSP source (one time, ~2GB)
  git clone https://android.googlesource.com/platform/hardware/interfaces aosp_source/hardware
  git clone https://android.googlesource.com/platform/system/sepolicy aosp_source/sepolicy
  git clone https://android.googlesource.com/platform/packages/services/Car aosp_source/car

Step 3 — Build RAG index (one time, ~10 min)
  python -m rag.aosp_indexer

Step 4 — Run baseline to get training examples for DSPy
  python multi_main_adaptive.py

Step 5 — Run DSPy optimization (one time per agent, ~30-60 min total)
  python dspy_opt/optimizer.py

Step 6 — Run three-way experiment
  python experiments/run_comparison.py

Step 7 — Analyze results
  python experiments/analyze_results.py
```

Agentic RAG + Domain Fine-tuned LLM + Structured Output + Evaluation Layer
Component                  ,Main Purpose in Enterprise Context,                        2026 Status
Agentic RAG,               Reliable knowledge access + multi-step reasoning,           Core / de facto standard
Domain Fine-tuned LLM,     "Consistency, domain expertise, cost/latency control",      Very common in production
Structured Output,         Safe downstream integration + parsing reliability,          Mandatory for anything automated
Evaluation Layer,          "Trust, monitoring, continuous improvement, compliance",    What separates PoC from production

--------------- Title ------------------------------------
Thesis: "Multi-Agent LLM Architectures for Automotive HAL Code Generation"

Chapter 1: Introduction (10 pages)
├─ Problem: HAL development is slow, error-prone
├─ Gap: Single-agent LLMs struggle with complex code
├─ Solution: Multi-agent architecture
└─ RQs: Effectiveness? Scalability? Quality?

Chapter 2: Background (15 pages)
├─ 2.1: AAOS and HAL architecture
├─ 2.2: VSS specification standard
├─ 2.3: LLMs for code generation
└─ 2.4: Multi-agent systems

Chapter 3: Related Work (15 pages)
├─ 3.1: Code generation with LLMs (Codex, AlphaCode)
├─ 3.2: Multi-agent software engineering (ChatDev, MetaGPT)
├─ 3.3: Domain-specific code generation
└─ 3.4: Automotive software development

Chapter 4: Methodology (25 pages)
├─ 4.1: Multi-agent architecture design
│   ├─ Design Doc Agent
│   ├─ Android App Agent  
│   ├─ Backend Agent
│   └─ HAL Architect Agent
├─ 4.2: Progressive generation strategy
├─ 4.3: LLM-First approach with fallback
└─ 4.4: Evaluation framework

Chapter 5: Implementation (15 pages)
├─ 5.1: System overview
├─ 5.2: Agent communication protocol
├─ 5.3: VSS→AAOS mapping
└─ 5.4: Code generation pipeline

Chapter 6: Evaluation (25 pages)
├─ 6.1: Research questions revisited
├─ 6.2: Experimental setup
├─ 6.3: RQ1: Effectiveness
│   ├─ Success rates (84.6%, 71.4%, 100%)
│   ├─ Compilation tests
│   └─ Code quality metrics
├─ 6.4: RQ2: Multi-agent vs Single-agent
│   ├─ Success rate comparison
│   ├─ Quality comparison
│   └─ Ablation study
├─ 6.5: RQ3: Scalability
│   ├─ 10, 50, 100, 500 signals
│   └─ Time/cost analysis
└─ 6.6: Case studies (3 examples)

Chapter 7: Discussion (12 pages)
├─ 7.1: Key findings
├─ 7.2: Implications for practice
├─ 7.3: Limitations
├─ 7.4: Threats to validity
└─ 7.5: Lessons learned

Chapter 8: Conclusion (5 pages)
├─ 8.1: Summary
├─ 8.2: Contributions
└─ 8.3: Future work

Total: ~120 pages + appendices

--------------- Roadmap ----------------------------------
Phase 1: Research Foundation (2-3 weeks)
1. Define Research Questions:
   RQ1: How effective are multi-agent LLM systems for generating 
        automotive HAL code compared to single-agent approaches?
   RQ2: What factors influence LLM generation success rates in 
        safety-critical automotive software?
   RQ3: Can progressive generation strategies improve code quality 
        and reliability?

2. Literature Review:
   - Survey code generation with LLMs (30+ papers)
   - Automotive software engineering (AUTOSAR, AAOS)
   - Multi-agent systems for SE
   - Identify research gaps

3. Define Hypothesis:
   "Multi-agent LLM architectures with adaptive generation strategies
    can achieve >80% automated code generation for production-quality
    automotive HAL implementations."


Phase 2: Methodology (3-4 weeks)
1. Baseline Implementations:
   - Single-agent LLM approach
   - Template-only generation
   - Your multi-agent system

2. Evaluation Framework:
   a) Compilation Success Rate
   b) AAOS Compliance (CTS tests)
   c) Code Quality Metrics (SonarQube, static analysis)
   d) Generation Time vs Quality Trade-offs
   e) Human Expert Evaluation (n=3-5 automotive engineers)

3. Dataset:
   - Small: 50 signals (current)
   - Medium: 200-300 signals
   - Large: 500-1000 signals (full VSS)

Phase 3: Experiments & Evaluation (6-8 weeks)
1. Controlled Experiments:
   - Vary: model size (7B, 13B, 70B), agent architecture, timeout strategies
   - Measure: success rate, quality, time, cost
   
2. Ablation Studies:
   - Impact of each agent (design doc, backend, app)
   - Progressive vs full generation
   - LLM-first vs template-first

3. Case Studies:
   - Deep dive into 3-5 complex modules
   - Failure analysis
   - Expert code review

4. Statistical Analysis:
   - Significance tests
   - Confidence intervals
   - Reproducibility across runs

Phase 4: Thesis Writing (4-6 weeks)
Structure:
1. Introduction (10 pages)
   - Motivation, problem statement, research questions
   
2. Background & Related Work (15-20 pages)
   - VSS, AAOS, HAL architecture
   - LLMs for code generation
   - Multi-agent systems
   
3. Methodology (20-25 pages)
   - System architecture
   - Agent design
   - Progressive generation algorithm
   - Evaluation framework
   
4. Experiments & Results (25-30 pages)
   - Quantitative results
   - Qualitative analysis
   - Ablation studies
   - Case studies
   
5. Discussion (10-15 pages)
   - Interpretation
   - Limitations
   - Threats to validity
   - Future work
   
6. Conclusion (5 pages)

Total: 85-115 pages + appendices    



--------------- Foundation --------------------------


## Official Thesis Title

**"Multi-Agent LLM Systems for Automotive HAL Code Generation:
Architecture, Strategies, and Evaluation"**


## Research Questions (FINAL)

### RQ1 (Primary): Effectiveness Evaluation
**"How effective are multi-agent LLM systems for generating 
production-quality automotive HAL code?"**

**Success Criteria:**
- Success rate: >80% LLM-generated code
- Compilation rate: >90% of generated code compiles
- Code quality: Comparable to human junior-mid level developers
- Spec compliance: >95% AAOS/VSS alignment

**Current Baseline (50 signals):**
- Design Doc Agent: 100% success ✓
- Android App Agent: 84.6% success ✓
- Backend Agent: 71.4% success ⚠️
- HAL Architect: 100% property match ✓

**What to measure:**
- [ ] Compilation success rate (does it build?)
- [ ] Runtime correctness (does it work?)
- [ ] Code quality metrics (cyclomatic complexity, maintainability index)
- [ ] AAOS specification compliance
- [ ] VSS property coverage

---

### RQ2 (Secondary): Factor Analysis
**"What architectural and strategic factors influence generation effectiveness?"**

**Factors to Investigate:**

1. **Architecture Comparison**
   - Multi-agent (your system)
   - Single-agent baseline
   - Template-based baseline
   - Hybrid approaches

2. **Agent Specialization** (Ablation Study)
   - Full system (4 agents)
   - Remove Design Doc agent
   - Remove Android App agent
   - Remove Backend agent
   - Remove HAL Architect
   - Measure impact of each

3. **Generation Strategies**
   - Full generation (0-30 properties)
   - Progressive/chunked (30-100 properties)
   - Iterative refinement (100+ properties)

4. **Model Size Impact**
   - 7B parameters (qwen2.5-coder:7b)
   - 13B parameters
   - 70B parameters (via API)

5. **Timeout Settings**
   - Conservative (2x current)
   - Standard (current: adaptive)
   - Aggressive (0.5x current)

**What to measure:**
- [ ] Success rate for each configuration
- [ ] Generation time and cost
- [ ] Code quality differences
- [ ] Statistical significance (p-value < 0.05)

---

### RQ3 (Tertiary): Scalability Assessment
**"How does the system scale across different automotive module sizes and domains?"**

**Scalability Dimensions:**

1. **Module Size**
   - Small: 10-20 signals
   - Medium: 50-100 signals ← Current
   - Large: 200-300 signals
   - Extra Large: 500+ signals

2. **Domain Diversity**
   - ADAS (current) ✓
   - Powertrain
   - Body
   - Chassis
   - Infotainment

3. **Efficiency Metrics**
   - Generation time vs module size
   - Cost per property (API tokens)
   - Quality degradation curve
   - Failure mode analysis

**What to measure:**
- [ ] Success rate across scales
- [ ] Time complexity (linear? quadratic?)
- [ ] Cost scaling
- [ ] Quality vs size trade-offs
- [ ] Breaking points (where does it fail?)

---

## Hypothesis

**Multi-agent LLM systems with progressive generation strategies will:**
1. Achieve >80% automated code generation (vs <50% single-agent)
2. Maintain >90% compilation success rate
3. Produce code quality comparable to junior-mid developers
4. Generate code 10x faster than manual development
5. Scale to 500+ signals with <20% quality degradation

---

## Success Metrics Summary

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| LLM Generation Rate | >80% | 84.6% (app) | ✓ |
| Compilation Success | >90% | TBD | TODO |
| Code Quality Score | >7/10 | TBD | TODO |
| Spec Compliance | >95% | 100% (match) | ✓ |
| Generation Speed | 10x manual | TBD | TODO |
| Scalability (500+) | <20% degradation | TBD | TODO |

---

## Timeline

**Phase 1: Foundation** (Weeks 1-2) ← YOU ARE HERE
- [x] Define research questions
- [x] Choose thesis title
- [ ] Literature review (50+ papers)
- [ ] Create evaluation framework

**Phase 2: Baseline & Validation** (Weeks 3-4)
- [ ] Implement single-agent baseline
- [ ] Validate current system (compilation tests)
- [ ] Establish measurement infrastructure
- [ ] Initial quality metrics

**Phase 3: Experiments** (Weeks 5-10)
- [ ] RQ1 experiments (effectiveness)
- [ ] RQ2 experiments (factors)
- [ ] RQ3 experiments (scalability)
- [ ] Statistical analysis

**Phase 4: Writing** (Weeks 11-16)
- [ ] Write thesis chapters
- [ ] Create visualizations
- [ ] Revisions and polish
- [ ] Defense preparation

**Total: 16 weeks (~4 months)**

---

## Contributions (Expected)

1. **Multi-agent architecture design** for automotive code generation
2. **Progressive generation strategy** with adaptive timeouts
3. **Comprehensive evaluation framework** for generated code quality
4. **Empirical study** of LLM effectiveness in safety-critical domains
5. **Open-source toolkit** for VSS→AAOS translation
6. **Best practices guide** for LLM-based automotive software development


---


