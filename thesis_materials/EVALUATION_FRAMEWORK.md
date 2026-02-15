# Evaluation Framework Design

## Overview
This framework evaluates multi-agent LLM systems for automotive HAL code generation
across three dimensions: Effectiveness (RQ1), Factors (RQ2), Scalability (RQ3).

---

## Part 1: Effectiveness Evaluation (RQ1)

### Test 1: Compilation Success
**Goal:** Does generated code compile?

**Method:**
```python
def test_compilation(output_dir):
    """
    Test if generated AIDL, C++, Java, Kotlin compile
    """
    results = {
        'aidl': compile_aidl_files(output_dir),
        'cpp': compile_cpp_files(output_dir),
        'java': compile_java_files(output_dir),
        'kotlin': compile_kotlin_files(output_dir)
    }
    return calculate_success_rate(results)
```

**Tools:**
- AIDL compiler (from AOSP SDK)
- g++/clang for C++
- javac for Java
- kotlinc for Kotlin

**Metrics:**
- Compilation success rate (%)
- Errors per file
- Error types (syntax, semantic, linking)

---

### Test 2: Code Quality Analysis
**Goal:** Measure code quality objectively

**Method:**
```python
def analyze_code_quality(file_path, language):
    """
    Run static analysis tools on generated code
    """
    if language == 'python':
        pylint_score = run_pylint(file_path)
        complexity = run_radon(file_path)
    elif language == 'java':
        pmd_score = run_pmd(file_path)
        complexity = run_checkstyle(file_path)
    elif language == 'cpp':
        cppcheck_score = run_cppcheck(file_path)
        complexity = run_lizard(file_path)
    
    return {
        'quality_score': pylint_score,
        'cyclomatic_complexity': complexity,
        'maintainability_index': calculate_mi(file_path)
    }
```

**Tools:**
- Python: pylint, radon, mypy
- Java/Kotlin: PMD, Checkstyle, SonarQube
- C++: cppcheck, clang-tidy, lizard

**Metrics:**
- Quality score (0-10)
- Cyclomatic complexity (target: <10 per function)
- Maintainability index (target: >60)
- Code smells count
- Duplicate code percentage

---

### Test 3: Specification Compliance
**Goal:** Does code meet AAOS/VSS specs?

**Method:**
```python
def verify_spec_compliance(generated_code, vss_spec):
    """
    Verify all VSS properties are correctly mapped to AAOS
    """
    checks = {
        'property_coverage': check_all_properties_mapped(generated_code, vss_spec),
        'type_correctness': verify_data_types(generated_code, vss_spec),
        'access_rights': verify_read_write_access(generated_code, vss_spec),
        'naming_conventions': check_aaos_naming(generated_code)
    }
    return calculate_compliance_score(checks)
```

**Metrics:**
- Property coverage: 100% expected
- Type correctness: 100% expected
- Access rights correctness: 100% expected
- Naming convention compliance: >95%

---

### Test 4: Runtime Correctness (if time permits)
**Goal:** Does code execute correctly?

**Method:**
- Deploy to AAOS emulator
- Send test commands via adb
- Verify responses match expected values

**Metrics:**
- Runtime errors (count)
- Response correctness (%)
- Performance (latency, throughput)

---

### Test 5: Human Expert Evaluation
**Goal:** Get qualitative feedback from experts

**Method:**
- Recruit 3-5 automotive software engineers
- Provide code samples (anonymized)
- Ask them to rate on 5-point Likert scale:
  - Code readability
  - Code correctness
  - Code maintainability
  - Production readiness
  - Overall quality

**Analysis:**
- Inter-rater reliability (Krippendorff's alpha)
- Average scores per dimension
- Qualitative feedback themes

---

## Part 2: Factor Analysis (RQ2)

### Experiment 2.1: Architecture Comparison

**Setup:**
```python
architectures = [
    'multi_agent_4',      # Your system (4 specialized agents)
    'single_agent',       # All in one LLM call
    'template_based',     # Template with variable substitution
    'hybrid'              # Templates + LLM enhancement
]

# Run same 50 signals through each
for arch in architectures:
    results[arch] = generate_code(vss_signals, architecture=arch)
    evaluate(results[arch])
```

**Measures:**
- Success rate comparison
- Quality score comparison
- Time and cost comparison
- Statistical significance test (t-test, ANOVA)

---

### Experiment 2.2: Ablation Study

**Setup:**
```python
configurations = [
    ['design_doc', 'android_app', 'backend', 'hal'],  # Full (baseline)
    ['android_app', 'backend', 'hal'],                 # No design doc
    ['design_doc', 'backend', 'hal'],                  # No android app
    ['design_doc', 'android_app', 'hal'],              # No backend
    ['design_doc', 'android_app', 'backend']           # No HAL
]

# Measure impact of removing each agent
for config in configurations:
    results[str(config)] = generate_code(vss_signals, agents=config)
    measure_degradation(results[str(config)])
```

**Measures:**
- Success rate delta
- Quality degradation
- Which agent is most critical?

---

### Experiment 2.3: Generation Strategy Comparison

**Setup:**
```python
strategies = [
    'full',              # Generate entire module at once
    'progressive_20',    # Chunk size: 20 properties
    'progressive_10',    # Chunk size: 10 properties
    'iterative'          # Generate, review, refine
]

# Test on 100 signal module
for strategy in strategies:
    results[strategy] = generate_code(vss_signals_100, strategy=strategy)
```

**Measures:**
- Success rate vs strategy
- Quality vs strategy
- Time vs strategy
- Optimal chunk size

---

### Experiment 2.4: Model Size Impact

**Setup:**
```python
models = [
    'qwen2.5-coder:7b',   # 7B params
    'qwen2.5-coder:14b',  # 14B params (if available)
    'gpt-4o',             # 70B+ (via API)
]

# Same inputs, different models
for model in models:
    results[model] = generate_code(vss_signals, llm_model=model)
```

**Measures:**
- Success rate vs model size
- Quality vs model size
- Cost vs model size
- Diminishing returns analysis

---

### Experiment 2.5: Timeout Sensitivity

**Setup:**
```python
timeout_multipliers = [0.5, 1.0, 2.0, 4.0]  # Relative to current adaptive timeout

for multiplier in timeout_multipliers:
    results[f'timeout_{multiplier}x'] = generate_code(
        vss_signals, 
        timeout_multiplier=multiplier
    )
```

**Measures:**
- Success rate vs timeout
- Cost-benefit analysis
- Optimal timeout value

---

## Part 3: Scalability Assessment (RQ3)

### Experiment 3.1: Module Size Scaling

**Setup:**
```python
module_sizes = [10, 25, 50, 100, 200, 500]

for size in module_sizes:
    vss_subset = sample_vss_signals(size)
    results[f'size_{size}'] = generate_code(vss_subset)
    measure_scalability(results[f'size_{size}'], size)
```

**Measures:**
- Success rate vs size
- Generation time vs size (linear? quadratic?)
- Quality degradation curve
- Cost per property
- Failure patterns

---

### Experiment 3.2: Domain Diversity

**Setup:**
```python
domains = ['ADAS', 'Powertrain', 'Body', 'Chassis', 'Infotainment']

for domain in domains:
    vss_domain = filter_vss_by_domain(domain)
    results[domain] = generate_code(vss_domain)
```

**Measures:**
- Success rate per domain
- Domain complexity analysis
- Cross-domain patterns

---

## Statistical Analysis Plan

### For all experiments:

1. **Descriptive Statistics**
   - Mean, median, std dev for all metrics
   - Visualizations (box plots, scatter plots)

2. **Hypothesis Testing**
   - t-tests for pairwise comparisons
   - ANOVA for multiple groups
   - Post-hoc tests (Tukey HSD)
   - Significance level: α = 0.05

3. **Effect Size**
   - Cohen's d for practical significance
   - Report both statistical and practical significance

4. **Reproducibility**
   - Run each experiment 3-5 times
   - Report confidence intervals (95%)
   - Random seed control

---

## Tools & Infrastructure

### Required Tools:
```bash
# Install evaluation tools
pip install pylint radon mypy pytest
pip install pandas numpy scipy matplotlib seaborn
pip install jupyter notebook

# Java/Kotlin tools
# Download PMD, Checkstyle, SonarQube

# C++ tools
sudo apt-get install cppcheck clang-tidy lizard
```

### Data Collection:
```python
# Create structured results database
import sqlite3

db = sqlite3.connect('evaluation_results.db')
db.execute('''
    CREATE TABLE experiments (
        id INTEGER PRIMARY KEY,
        experiment_name TEXT,
        rq TEXT,
        configuration TEXT,
        timestamp DATETIME,
        success_rate REAL,
        quality_score REAL,
        compilation_rate REAL,
        generation_time REAL,
        cost REAL,
        notes TEXT
    )
''')
```

---

## Validation Checklist

Before running full experiments:
- [ ] All tools installed and tested
- [ ] Baseline system validated (current 50-signal output)
- [ ] Single-agent baseline implemented
- [ ] Template baseline implemented
- [ ] Data collection infrastructure ready
- [ ] Statistical analysis scripts prepared
- [ ] Experiment scripts debugged on small samples
- [ ] Results database schema finalized
- [ ] Advisor approves evaluation plan

---

## Expected Timeline

- Week 1: Tool setup, infrastructure
- Week 2: RQ1 experiments (effectiveness)
- Week 3-4: RQ2 experiments (factors)
- Week 5-6: RQ3 experiments (scalability)
- Week 7: Statistical analysis
- Week 8: Validation and reproducibility checks

---

## Notes
- Start small: validate on 10 signals before scaling to 500
- Document everything: unexpected results are often most interesting
- Save all raw data: you'll need it for the thesis appendix
- Take screenshots/videos: helpful for defense presentation