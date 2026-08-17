# Tài liệu kỹ thuật — LLM-Based Code Generation for AAOS VHAL
*Phần A: Kiến trúc & thuật toán (advanced). Phần B: Kiến thức nền AI/LLM (basic).*

---

# PHẦN A — KIẾN TRÚC & THUẬT TOÁN

## 0. VHAL Property ID — cấu trúc bit 32-bit thật

Đây là kiến thức AOSP nền tảng, hay bị hỏi sâu vì liên quan trực tiếp tới "vì sao property phải encode đúng mới hoạt động":

```
Property ID (32-bit) = group (0xF0000000) | area (0x0F000000) | type (0x00FF0000) | index (0x0000FFFF)
```

**Giá trị thật project dùng** (`multi_main_c5.py::encode_prop_id`):

```python
VSS_GROUP = 0x20000000  # VehiclePropertyGroup::VENDOR — bắt buộc cho property tự định nghĩa,
                        # không phải property chuẩn AOSP (những cái đó dùng group SYSTEM=0x10000000)
VSS_AREA  = 0x01000000  # VehicleArea::GLOBAL — property không gắn với 1 vùng vật lý cụ thể
                        # (khác property như cửa sổ/ghế — những cái đó cần area riêng cho từng cửa/ghế)
VSS_TYPE_BITS = {
    "STRING":  0x00100000,
    "BOOLEAN": 0x00200000,
    "INT32":   0x00400000,
    "INT64":   0x00500000,
    "FLOAT":   0x00600000,
}

def encode_prop_id(raw_index, vss_type):
    if raw_index & 0xF0000000:  # đã là ID đầy đủ — giữ nguyên
        return raw_index
    type_bits = VSS_TYPE_BITS.get(vss_type.upper(), 0x00400000)
    return VSS_GROUP | VSS_AREA | type_bits | (raw_index & 0xFFFF)
```

**Vì sao KHÔNG thể dùng index trần (VD `0x1000`) làm property ID:** thiếu hết group/area/type bits → VHAL coi là ID không hợp lệ ngay từ bước validate config → property **âm thầm không đăng ký được**, không có lỗi rõ ràng, chỉ đơn giản là biến mất khỏi danh sách property khả dụng.

**Vì sao type phải nằm TRONG chính property ID, không phải field riêng:** `VehiclePropConfig` (struct AIDL thật) không có field "type" độc lập — VHAL và `CarPropertyManager` đọc type bằng cách **mask ra đúng 8 bit type** từ property ID. Đây là lý do bug "encode sai type bits" gây lỗi kiểu dữ liệu ở tầng framework, không phải lỗi compile.

**Ví dụ thật:** `0x21204010` = `VENDOR(0x20000000) | GLOBAL(0x01000000) | BOOLEAN(0x00200000) | index(0x4010)`.

---

## 1. Pipeline tổng thể

```
VSS Spec (500 signal) → Labelling (gán domain+type) → Module Planner (500→7 module)
    → RAG+DSPy Agents (AIDL / CPP / SELinux / Build, song song mỗi module)
    → Validate + Retry (chỉ C4)
    → VssGlueAgent gộp 7 domain thành 1 Aggregator
```

4 điều kiện thực nghiệm chỉ khác nhau ở tầng "RAG+DSPy Agents":

| | C1 Baseline | C2 Adaptive | C3 RAG+DSPy | C4 Feedback |
|---|---|---|---|---|
| RAG retrieval | ✗ | ✗ | ✓ | ✓ |
| DSPy/MIPROv2 | ✗ | ✗ | ✓ | ✓ |
| Prompt-variant bandit | ✗ | ✓ | ✗ | ✗ |
| Validate→retry loop | ✗ | ✗ | ✗ | ✓ |

---

## 1b. VHALAidlAgent — có gì? (baseline C1, lõi dùng chung)

**File:** `agents/vhal_aidl_agent.py` — class `VHALAidlAgent`. Đây là agent baseline (C1) sinh file `.aidl` enum property; C3/C4 bọc thêm RAG+DSPy nhưng vẫn **fallback** về lõi này khi DSPy fail.

### Trách nhiệm chính
1. **Parse** danh sách property từ YAML/JSON module spec.
2. **Chunk** nếu > `CHUNK_SIZE=15` property (tránh timeout 32B model).
3. **Build prompt tĩnh** + system prompt cố định → gọi `call_llm(..., temperature=0.0, response_format="json")`.
4. **Parse JSON** `{"files":[{"path","content"}]}` → ghi file qua `SafeWriter`.
5. **Merge enum** từ các chunk + re-assign sequential ID (hoặc fallback deterministic nếu LLM trả 0 entry).
6. **Repair pass** 1 lần nếu JSON invalid; sau đó deterministic fallback.

### System prompt tĩnh (minh họa)

```python
self.system_prompt = (
    "You are an expert Android Automotive OS (AAOS) Vehicle HAL engineer.\n"
    "Generate correct, production-grade AIDL files from the provided VSS spec.\n"
    "You MUST output ONLY valid JSON. No explanations, no markdown, no code blocks.\n"
    'If you cannot produce perfect JSON, output exactly: {"files": []}\n\n'
    "CRITICAL RULES FOR ANDROID 14+ AIDL:\n"
    f"- Generate ONLY a {self.enum_name}.aidl enum file — do NOT generate IVehicle.aidl,\n"
    "  IVehicleCallback.aidl, or VehiclePropValue.aidl (these already exist in AOSP).\n"
    "- Use package: android.hardware.automotive.vehicle\n"
    "- Add @VintfStability annotation\n"
    "- Use @Backing(type=\"int\") for enum\n"
    "- Do NOT use HIDL patterns (no V2_0 suffix, ...)\n"
    "- The generated file must be ADDITIVE to the existing AOSP tree, not a replacement."
)
```

→ **Prompt tĩnh** = instruction cố định, không thay đổi theo score/feedback (khác C2 bandit / C3 DSPy-optimised / C4 error-feedback).

### User prompt tĩnh (minh họa `build_prompt`)

```python
return (
    "Generate complete Vehicle HAL AIDL files including vendor-specific property enum.\n"
    "MANDATORY OUTPUT FORMAT:\n"
    "Return ONLY valid JSON:\n"
    '{\n  "files": [ {"path": "...", "content": "..." } ]\n}\n\n'
    "CRITICAL RULES:\n"
    "- Output ONLY the JSON object. No extra text, no fences, no comments.\n"
    f"REQUIRED FILES:\n- .../{self.enum_name}.aidl\n\n"
    f"VSS PROPERTIES TO INCLUDE IN {self.enum_name}.aidl:\n"
    f"- PROP_A (BOOLEAN, READ_WRITE)\n- PROP_B (INT32, READ)\n...\n\n"
    f"{self.enum_name}.aidl example structure:\n"
    "@VintfStability\n"
    '@Backing(type="int")\n'
    f"enum {self.enum_name} {{\n"
    "    PROP_A = 0xF0000000,\n"
    "    PROP_B = 0xF0000001,\n"
    "    // ... continue sequentially\n"
    "}\n\n"
    f"FULL VSS SPEC:\n{plan_text}\n\n"
    "OUTPUT ONLY THE JSON NOW:"
)
```

### ID canonical + fallback (minh họa)

LLM **không** được tin tưởng gán ID. Pipeline luôn **đóng dấu** lại 32-bit AAOS ID:

```python
# agents/rag_dspy_aidl_agent.py — _aaos_encode (C3/C4)
_VSS_GROUP = 0x20000000  # VENDOR
_VSS_AREA  = 0x01000000  # GLOBAL
_TYPE_BITS = {
    "BOOLEAN": 0x00200000, "INT": 0x00400000, "FLOAT": 0x00600000,
    "STRING": 0x00100000, "INT64": 0x00500000, ...
}

def _aaos_encode(local_id: int, vtype: str = "INT") -> int:
    type_bits = _TYPE_BITS.get((vtype or "INT").upper(), 0x00e00000)
    return _VSS_GROUP | _VSS_AREA | type_bits | (local_id & 0xFFFF)

# Sau LLM sinh enum → _reencode_enum_output:
# "Force every enum constant to the correct full 32-bit AAOS ID,
#  regardless of what the LLM wrote."
correct_id = hex(_aaos_encode(base + j, prop_type))
line = f"    {name} = {correct_id},"
```

**Fallback deterministic** (C1, khi LLM trả 0 entry hoặc JSON fail):

```python
def _write_fallback_vss(self, props: list) -> None:
    lines = []
    for i, p in enumerate(props):
        name = p.get("name", f"PROP_{i}")
        lines.append(f"    {name} = 0xF{i:07X},")  # sequential, không phụ thuộc LLM
    # viết enum hoàn chỉnh từ list property thô
```

→ Dù LLM hallucinate ID hoặc fail hoàn toàn, file `.aidl` cuối cùng **luôn** có ID 32-bit hợp lệ (VENDOR|GLOBAL|TYPE|index). DOMAIN_BASE giữ offset local theo domain (adas=0x1000, body=0x2000, …) rồi encode full 32-bit.

### Domain-aware (bug đã sửa)

Trước đây hardcoded `VehiclePropertyAdas` → mọi domain ghi đè cùng 1 file. Hiện:

```python
self.domain_cap = self.domain.capitalize()
self.enum_name = f"VehicleProperty{self.domain_cap}"  # Adas → VehiclePropertyAdas.aidl
```

---

## 2. RAG Retrieval — kiến trúc hybrid 3 kênh (không chỉ embedding đơn thuần)

### 2.1. Indexing — cách corpus được chia nhỏ

```python
CHUNK_SIZE_WORDS    = 400   # mỗi chunk ~400 từ
CHUNK_OVERLAP_WORDS = 50    # chồng lấp 50 từ giữa 2 chunk liên tiếp
```

Overlap tồn tại để tránh cắt đứt 1 method signature/block logic đúng ngay ranh giới chunk.

**HIDL filter Layer 1 (lúc index):** loại trừ file theo path TRƯỚC KHI đưa vào ChromaDB.

**ChromaDB config:** `metadata={"hnsw:space": "cosine"}` — dùng **HNSW** (Hierarchical Navigable Small World — approximate nearest-neighbor search, dạng graph-based) để index vector, cosine distance làm metric khoảng cách.

### 2.2. Retrieval — 3 kênh độc lập

**Kênh 1 — Dense retrieval (embedding + ChromaDB):**

```python
embedding = self._embed(query)   # SentenceTransformer.encode(..., normalize_embeddings=True)
raw = col.query(query_embeddings=[embedding], n_results=..., include=["documents","metadatas","distances"])
score = round(1.0 - dist, 4)     # cosine distance → cosine similarity
```

**Kênh 2 — BM25 sparse retrieval (keyword-based, TF-IDF-style):**

```python
# rag/aosp_retriever.py
self._bm25_index[col_name] = BM25Okapi([c["text"].split() for c in corpus])
scores = self._bm25_index[col_name].get_scores(query.split())
```

**TF-IDF-style nghĩa là gì (giải thích + minh họa):**
- **TF (Term Frequency):** từ xuất hiện càng nhiều trong 1 chunk → điểm càng cao (nhưng BM25 *bão hòa* — lần thứ 10 không quan trọng gấp 10 lần lần thứ 1).
- **IDF (Inverse Document Frequency):** từ hiếm trong toàn corpus (vd. `getAllPropertyConfigs`, `VehiclePropValue`) được **boost** mạnh; từ phổ biến (`the`, `return`, `int`) gần như bị bỏ qua.
- BM25 = biến thể hiện đại của TF-IDF: thêm chuẩn hóa theo độ dài document + tham số \(k_1, b\).

**Ví dụ trực quan:**

| Query token | Dense (embedding) | BM25 |
|-------------|-------------------|------|
| `getAllPropertyConfigs` | Có thể match chunk nói về "lấy config property" (ngữ nghĩa) | Chỉ match chunk **chứa đúng chuỗi** `getAllPropertyConfigs` |
| `IVehicleHardware` | Có thể match interface tương tự | Bắt đúng tên class AOSP |

→ Hybrid = dense bắt ngữ nghĩa + BM25 bắt identifier chính xác (tên hàm/type AOSP) mà embedding dễ bỏ lỡ.

**Kênh 3 — Cross-encoder reranker (sau khi gộp kênh 1+2):**

```python
RERANKER_MODEL = "BAAI/bge-reranker-base"
pairs = [[combined_query, r["text"]] for r in merged]
scores = self._reranker.predict(pairs)
```

Cross-encoder khác bi-encoder (dùng cho embedding): bi-encoder encode query và document RIÊNG BIỆT rồi so cosine (nhanh, pre-compute được); cross-encoder encode CẢ CẶP CÙNG LÚC qua model (chính xác hơn nhưng chậm, không pre-compute được) — nên chỉ dùng để RERANK top-N đã lọc thô, không search toàn corpus.

### 2.3. Tham số thật — đã tinh chỉnh qua thực nghiệm

```python
DEFAULT_TOP_K       = 6      # comment gốc: "was 3 — cpp method signatures need more chunks"
MIN_SCORE_THRESHOLD = 0.25
max_chars_per_chunk = 1500   # comment gốc: "was 800 — sigs were truncated"
```

Cả 2 giá trị đều TĂNG so với bản đầu (3→6, 800→1500) — dấu hiệu tinh chỉnh dựa trên quan sát thực tế (signature C++ dài, dễ bị cắt cụt ở ngưỡng thấp).

### 2.4. HIDL filter Layer 2 — vị trí chính xác

```python
def _parse_results(self, raw, top_k):
    HIDL_PATH = ("/2.0/", "/1.0/", "/3.0/", "/hidl/", ...)
    if any(m in meta.get("file","").lower() for m in HIDL_PATH):
        continue   # loại tại bước parse kết quả, trước khi vào prompt
```

### 2.5. Layer 3 — HIDL filter — chỉ hard drop, không có soft penalty

Chunk dính legacy bị loại tuyệt đối, không được giữ lại với điểm thấp hơn.

> **Ghi chú phòng vấn:** Thesis PDF mô tả 3-layer (có soft penalty ×0.5 ở Layer 3). Implementation cuối cùng chỉ còn **2-layer hard drop** (Layer 1 index-time + Layer 2 query-time). Khi bị hỏi có thể nói: "Thiết kế ban đầu có soft penalty, nhưng thực nghiệm cho thấy hard drop sạch hơn và ổn định hơn nên đã bỏ soft penalty."

### 2.6. Format cuối cùng đưa vào prompt

```python
lines = [f"### {label}", f"# {len(retrieved)} example(s) from real AOSP source.", "# Match signatures exactly.", ""]
for i, r in enumerate(retrieved, 1):
    lines += [f"// --- Example {i} | {r['filename']} | score: {r['score']:.2f} ---", text, ""]
```

LLM thấy rõ từng example kèm điểm số + tên file gốc, không phải context vô danh. Store: ChromaDB, 7 collection theo domain, 24,245 chunk.

---

## 3. DSPy + MIPROv2 — prompt optimization

### 3.0. Kiến trúc lớp bên trong DSPy — Signature / Module / Predictor

Ba khái niệm cốt lõi, hay bị hỏi phân biệt:

| Khái niệm | Vai trò | Trong project |
|---|---|---|
| **Signature** | Khai báo CONTRACT — input field nào, output field nào, docstring làm instruction gốc | `hal_signatures.py` — VD `CppVehicleHardwareSignature(domain, properties, aosp_context -> reasoning, read_cases, write_cases)` |
| **Module** | Đóng gói 1 Signature + chiến lược gọi LLM (bao nhiêu lần, có "suy nghĩ" hay không) | `dspy.ChainOfThought(SIGNATURE_CLASS)` |
| **Predictor** | Instance THẬT thực thi — giữ trạng thái đã compile (instruction cuối + demo đã chọn) | `self.entries_predictor`, `self.register_body_predictor` |

**`dspy.ChainOfThought` làm gì khác `dspy.Predict` (bản thường):** tự động chèn THÊM 1 output field ẩn tên `reasoning` VÀO TRƯỚC các field thật trong Signature, buộc LLM phải viết ra chuỗi suy luận trước khi điền các field chính thức — cơ chế "chain of thought" chính là ép model qua bước trung gian này trước khi trả lời, không cần người dùng tự viết prompt "hãy suy nghĩ từng bước".

**Vòng đời 1 lệnh gọi predictor:**

```
predictor(domain=X, properties=Y, aosp_context=Z)
    → build prompt = instruction (từ program.json nếu có, else Signature docstring)
                      + demo examples (few-shot, nếu MIPROv2 đã chọn)
                      + input fields hiện tại (domain, properties, aosp_context)
    → gửi tới Ollama (Qwen2.5-Coder)
    → JSONAdapter parse response thành đúng shape Signature khai báo
    → trả về object có .reasoning, .read_cases, .write_cases (attribute access)
```

**Vì sao `getattr(result, "read_cases", "") or ""` xuất hiện khắp nơi trong code:** nếu JSONAdapter parse THIẾU 1 field (model không sinh đủ, ví dụ dừng giữa chừng) — object trả về vẫn tồn tại nhưng field đó KHÔNG có attribute, `getattr` với default tránh `AttributeError` sập chương trình.

### 3.1. Không cần gold-standard label

Không có "đáp án đúng" cho VHAL C++ làm nhãn huấn luyện. MIPROv2 optimize dựa trên **metric function** (structural score từ `validators.py`), không so khớp với file mẫu.

### 3.2. Quy trình (minh họa cụ thể từ `dspy_opt/optimizer.py`)

**Thứ tự chạy thật trong thesis:**

```
1. multi_main_adaptive.py     → sinh VSS_LABELLED_500.json (train data)
2. dspy_opt/optimizer.py      → MIPROv2 / LabeledFewShot → dspy_opt/saved/<agent>_program/
3. multi_main_rag_dspy.py     → load program đã optimise lúc generate
```

**Bên trong `optimise_one(agent_type)` — 6 bước:**

```python
# 1. Lấy module + metric
module_class, _ = MODULE_REGISTRY[agent_type]
module    = module_class()          # dspy.ChainOfThought(SIGNATURE)
metric_fn = METRIC_REGISTRY[agent_type]  # structural+syntax+coverage

# 2. Build trainset từ labelled VSS (không cần gold output)
trainset = TrainingSetBuilder(...).build(agent_type, n=train_size)
# mỗi Example = {domain, properties, aosp_context}  — metric chấm, không so file mẫu

# 3. Score baseline (chưa optimise) trên 2 example
baseline_scores = _evaluate_sample(module, metric_fn, trainset[:2], ...)

# 4. Chọn chiến lược
if agent_type in MIPRO_SKIP_AGENTS:   # {"aidl","design_doc","selinux","build"}
    # Ceiling agents: LabeledFewShot k=2 (~2 LLM calls)
    optimised = dspy.LabeledFewShot(k=2).compile(module, trainset=trainset)
else:
    # Full MIPROv2 (~20–36 LLM calls với auto="medium")
    optimizer = dspy.MIPROv2(
        metric=metric_fn,
        auto="medium",       # tự tính num_candidates / num_trials
        num_threads=1,       # Ollama serialize — >1 chỉ thêm overhead
        verbose=False,
    )
    optimised = optimizer.compile(
        module, trainset=trainset,
        requires_permission_to_run=False,
        program_aware_proposer=False,  # proposer không đọc source code module
    )

# 5. Score bản optimise; chỉ lưu nếu ≥ baseline
# 6. module.save("dspy_opt/saved/<agent>_program/")  → program.json
```

**MIPROv2 bên trong làm gì (3 pha search) — minh họa chi tiết:**

```
┌─────────────────────────────────────────────────────────────────┐
│  Pha 1: Bootstrap demos                                         │
│  Pha 2: Propose instruction candidates                          │
│  Pha 3: Bayesian trial search (instruction × demo-set)          │
│                         ↓                                       │
│  Lưu program.json = (instruction tối ưu + demo-set tối ưu)      │
└─────────────────────────────────────────────────────────────────┘
```

#### Pha 1 — Bootstrap few-shot demos (giải thích chi tiết, không mơ hồ)

**Trả lời thẳng: “Chạy prompt gốc trên trainset ra cái gì?”**

→ **Ra code do LLM tự sinh** (C++ / AIDL / SELinux / … tùy agent), **không** ra điểm số, **không** ra file mẫu có sẵn.

| | Nội dung |
|---|----------|
| **Input** (có trong trainset) | `domain`, `properties`, `aosp_context` — **không** có đáp án vàng |
| **Prompt gốc** | docstring Signature + 3 field input ở trên (chưa có few-shot) |
| **Output** | code HAL thật, vd. `case VehicleProperty::DOOR_ISOPEN: { readRegister(...); }` + field `reasoning` (ChainOfThought) |
| **Bước tiếp** | metric chấm cặp (input → output vừa sinh); score cao → **giữ làm demo**, score thấp → bỏ |

Tóm lại: chạy prompt gốc = **generate thử** trên từng example; metric lọc output nào đủ tốt để dùng làm few-shot sau này.

---

**Mục tiêu pha này:** thu thập vài cặp (input → output) mà model *hiện tại* (chưa optimise) đã sinh ra **tốt theo metric** — dùng làm few-shot example cho các lần generate sau. Không cần người gán nhãn đúng/sai.

**Trainset trông như thế nào?**  

Mỗi example chỉ có **input**, không có “đáp án vàng”:

```python
# TrainingSetBuilder.build() — dspy.Example
Ex1 = {
  "domain":       "Body",
  "properties":   "- Name: VEHICLE_BODY_DOOR_ISOPEN\n  Type: BOOLEAN\n  Access: READ_WRITE",
  "aosp_context": "### AOSP Reference\n// Example from VehicleHardware.cpp ...",
}
# KHÔNG có field "aidl_code" / "cpp_impl" đúng sẵn
```

**“Chạy prompt gốc” nghĩa là gì — từng bước:**

```
Bước A. Lấy module chưa optimise
        module = CppHALModule()   # dspy.ChainOfThought(CppSignature)
        # instruction lúc này = docstring trong Signature
        #   "Generate C++ readRegister/writeRegister cases for domain..."
        # demos = []  (chưa có few-shot)

Bước B. Chọn 1 example từ trainset, gọi predictor
        pred = module(
            domain=Ex1.domain,
            properties=Ex1.properties,
            aosp_context=Ex1.aosp_context,
        )
        # Bên trong DSPy:
        #   build prompt = instruction + (chưa có demo) + input fields
        #   gửi Ollama (Qwen2.5-Coder, temperature≈0.25)
        #   parse JSON/text → pred.cpp_impl, pred.reasoning, ...

Bước C. Metric chấm — KHÔNG so với file mẫu
        score = metric_cpp(Ex1, pred)
        # = 0.35*structural + 0.45*syntax(clang) + 0.20*coverage
        # structural: có #include, class, getAllPropertyConfigs, không HIDL?
        # syntax:     clang++ -fsyntax-only có error không?
        # coverage:   tên DOOR_ISOPEN có xuất hiện trong output không?

Bước D. Quyết định giữ / bỏ
        if score đủ cao (vd. ≥ metric_threshold hoặc top theo số lượng cần):
            GIỮ cặp (Ex1.inputs → pred.outputs) làm bootstrapped demo
        else:
            BỎ — thử example khác từ trainset
```

**Ví dụ số cụ thể (agent cpp, 1 example):**

| Thành phần | Nội dung |
|------------|----------|
| **Input đưa vào LLM** | domain=`Body`, properties=`DOOR_ISOPEN (BOOLEAN, READ_WRITE)`, aosp_context=chunk `IVehicleHardware.h` |
| **Prompt gốc** | docstring Signature + input ở trên (chưa có few-shot) |
| **Output LLM sinh** | `case static_cast<int32_t>(VehicleProperty::DOOR_ISOPEN): { ... readRegister(...); }` |
| **Metric chấm** | struct=0.90 (đủ keyword AOSP), syntax=1.0 (clang pass), coverage=1.0 (có `DOOR_ISOPEN`) → **score = 0.35×0.9 + 0.45×1.0 + 0.20×1.0 = 0.965** |
| **Quyết định** | 0.965 cao → **GIỮ** thành demo: “khi input Body/DOOR_ISOPEN thì output phải giống đoạn case này” |

Nếu LLM sinh thiếu `case`, hoặc dùng HIDL `Return<>`, hoặc clang fail → score thấp (vd. 0.4) → **bỏ**, lấy Ex2 thử tiếp.

**Kết quả pha 1:**
- `MAX_BOOTSTRAPPED_DEMOS=1` → mỗi candidate set chỉ cần **1** demo tốt (ít LLM call).
- Đồng thời lấy thêm `max_labeled_demos=2` example **chỉ input** (không output) từ trainset để bổ sung context.
- Demo đã bootstrap = bằng chứng “model *đã từng* làm đúng bài này” — khác gold label do người viết sẵn.

**Vì sao không cần gold label?**  
Metric tự chấm cấu trúc/cú pháp/coverage. “Đúng” = compile được + đủ pattern AOSP + cover property — đủ để lọc demo chất lượng cho few-shot, không cần file C++ mẫu viết tay.

#### Pha 2 — Propose instruction candidates

LLM *khác* (prompt_model, cùng Qwen2.5-Coder) được giao nhiệm vụ **viết lại instruction**, không sinh code HAL.

Proposer nhận 4 nguồn grounding:
1. **Dataset summary** — tóm tắt trainset (domain nào, type property phổ biến…)
2. **Program summary** — cấu trúc Signature/Module (trừ khi `program_aware_proposer=False` như project đang set)
3. **Bootstrapped demos** từ Pha 1 — ví dụ input/output thật
4. **Random tip** — “be creative”, “be concise”, “focus on edge cases”… để đa dạng không gian instruction

```
Signature docstring gốc (hal_signatures.py):
  "Generate C++ VehicleHardware implementation for the given domain..."

→ Proposer có thể viết thành:
  "You are an AAOS VHAL engineer. Emit ONLY the readRegister/writeRegister
   switch cases. Match AOSP IVehicleHardware signatures exactly. Never use
   HIDL patterns. Each property must map to a real file I/O path..."
```

→ Instruction cuối **có thể khác hẳn** docstring trong source — đây là lý do gọi “optimised program”, tách biệt khỏi code Signature.

Project set `program_aware_proposer=False` → proposer **không** đọc source code module khi viết instruction (giảm phụ thuộc cấu trúc code, tập trung vào data + tip).

#### Pha 3 — Bayesian trial search

Không brute-force mọi cặp. Dùng **Bayesian Optimization** (surrogate model, kiểu Tree-structured Parzen Estimator / Optuna):

```
candidates:
  instructions = [I₀=docstring gốc, I₁, I₂, … Iₖ]     # từ Pha 2
  demo_sets    = [D₀, D₁, … Dₘ]                       # từ Pha 1

for trial in 1..num_trials:          # auto="medium" → DSPy tự chọn ~20–36
    # Surrogate model đề xuất cặp (Iᵢ, Dⱼ) có kỳ vọng score cao
    program.predictor.instruction = Iᵢ
    program.predictor.demos = Dⱼ
    score = mean( metric_fn(ex, program(ex)) for ex in minibatch )
    # Cập nhật surrogate: “cặp này tốt/xấu”
return cặp (I*, D*) điểm cao nhất trên full valset
```

- Mỗi trial = 1 lần gắn instruction + demo → chạy metric trên minibatch train/val.
- Surrogate học dần: instruction nào + demo nào tương tác tốt → trial sau ưu tiên vùng đó (explore/exploit).
- `auto="medium"` tự tính `num_candidates` / `num_trials` nội bộ; **không** được truyền `max_bootstrapped_demos` cùng lúc với `auto=` (raise ValueError).

**Minh họa số (agent cpp, giả định):**

| Trial | Instruction | Demo-set | Minibatch score |
|-------|-------------|----------|-----------------|
| 1 | I₀ (gốc) | D₀ | 0.78 |
| 2 | I₁ (chi tiết AOSP) | D₀ | 0.85 |
| 3 | I₁ | D₁ (coverage cao) | **0.91** ← best so far |
| … | … | … | … |
| 24 | I₂ | D₁ | 0.88 |

→ Lưu `program.json` chứa instruction I₁ + demos D₁.

#### Config thật trong project

```python
MIPRO_AUTO_SETTING     = "medium"
MAX_BOOTSTRAPPED_DEMOS = 1
MAX_LABELED_DEMOS      = 2
DEFAULT_TRAIN_SIZE     = 2
MIPRO_SKIP_AGENTS      = {"aidl", "design_doc", "selinux", "build"}
# cpp KHÔNG skip — chạy full MIPROv2 (~20–36 LLM calls)
# ceiling agents → LabeledFewShot(k=2) chỉ ~2 calls
```

**Điểm hay bị hỏi:**
- MIPROv2 = **black-box search** trên prompt (không gradient, không đụng trọng số model).
- Metric = structural score từ `validators.py` — **không cần gold-standard label**.
- Instruction trong `program.json` có thể **khác** docstring Signature — lúc inference `load()` ghi đè instruction đã optimise.
- `program_aware_proposer=False` trong project → proposer không đọc code module khi propose.

### 3.3. Kiến trúc lưu trữ — Signature vs Compiled Program

```python
class _BaseHALModule(dspy.Module):
    def __init__(self):
        self.generate = dspy.ChainOfThought(self.SIGNATURE_CLASS)  # đọc signature docstring
    def load(self, path):
        # nếu có file đã lưu, GHI ĐÈ instruction bằng bản đã optimize
        ...
```

Instruction thật LLM nhận có thể KHÁC docstring gốc trong source — MIPROv2 không chỉ chọn demo, còn có thể **tự viết lại câu chữ instruction** rồi lưu cố định. Đây là lý do program đã compile được gọi là "optimised program", tách biệt khỏi source code signature.

### 3.4. Ceiling agents — bypass MIPROv2

```python
MIPRO_SKIP_AGENTS = {"aidl", "design_doc", "selinux", "build"}
MAX_LABELED_DEMOS = 2
```

**Chỉ số ít agent (cpp và các agent KHÔNG nằm trong set trên) thực sự chạy full MIPROv2 search** — 4 agent kể trên dùng `LabeledFewShot(k=2)` (2 demo, ~2 lệnh gọi), không phải MIPROv2 đầy đủ (~36 lệnh gọi). Lý do: các agent này cho thấy zero trial variance (điểm không đổi dù thử instruction/demo khác nhau) — đã chạm trần khả năng cải thiện qua prompt, chạy full search chỉ tốn thêm ngân sách vô ích.

**Config thật của MIPROv2 khi CÓ chạy (agent cpp):**

```python
optimizer = dspy.MIPROv2(
    metric=metric_fn,
    auto="medium",                 # MIPRO_AUTO_SETTING — cân bằng ngân sách search vs thời gian
    num_threads=1,                 # Ollama serialize request thật sự — nhiều thread chỉ thêm overhead, không tăng tốc
    verbose=False,
)
optimised_module = optimizer.compile(
    module, trainset=trainset,
    requires_permission_to_run=False,   # tự động chạy, không cần xác nhận tương tác
    program_aware_proposer=False,       # bộ đề xuất instruction KHÔNG đọc cấu trúc code chương trình khi propose
)
```

**`auto="medium"` quyết định gì:** khi dùng `auto=`, MIPROv2 tự tính `num_candidates`/`num_trials` nội bộ — không được truyền `max_bootstrapped_demos`/`max_labeled_demos` cùng lúc (raise `ValueError` nếu truyền cả 2). `"medium"` là mức cân bằng giữa `"light"` (nhanh, ít trial) và `"heavy"` (chậm, nhiều trial, tìm kỹ hơn).

---

## 4. C2 — Adaptive Prompt Selection (minh họa cụ thể)

C2 **không** dùng RAG/DSPy. Thêm 2 lớp adaptive trên baseline:

1. **Prompt variant** — UCB1 + ε-greedy (`adaptive_components/prompt_selector.py`)
2. **Chunk size** — Thompson Sampling / Beta posterior (`adaptive_components/chunk_size_optimizer.py`)

### 4.1. Prompt variant — UCB1 + ε-greedy (KHÔNG phải Thompson Sampling)

**4 variant tĩnh:**

| Key | Ý nghĩa |
|-----|---------|
| `minimal` | Concise, essential error handling only |
| `detailed` | Full comments, defensive programming |
| `conservative` | Safe proven AOSP patterns only |
| `aggressive` | Modern C++17/20, RAII, performance |

**Bucket theo số property:**

```python
def _get_property_range(self, count: int) -> str:
    if count <= 10: return "tiny"
    elif count <= 30: return "small"
    elif count <= 50: return "medium"
    elif count <= 100: return "large"
    else: return "xlarge"
```

#### Giải thích UCB1 (Upper Confidence Bound 1)

UCB1 là thuật toán **multi-armed bandit** cổ điển. Mỗi prompt variant = 1 “arm”. Mục tiêu: cân bằng **exploit** (chọn cái đang tốt nhất) và **explore** (thử cái còn ít dữ liệu).

**Công thức UCB1 gốc:**

\[
\text{score}(a) = \underbrace{\bar{x}_a}_{\text{success rate}} + \underbrace{c \sqrt{\frac{2\ln N}{n_a}}}_{\text{uncertainty bonus}}
\]

Trong đó:
- \(\bar{x}_a\) = success_rate của variant \(a\)
- \(n_a\) = số lần đã thử variant \(a\)
- \(N\) = tổng số lần thử tất cả variant trong bucket hiện tại
- \(c\) = hệ số (trong code project dùng \(c = 0.1\))

**Ý nghĩa từng phần:**
- **success_rate cao** → ưu tiên exploit (cái đang thắng).
- **uncertainty cao** khi \(n_a\) nhỏ → ưu tiên explore (cái còn ít dữ liệu).
- Khi \(n_a\) tăng, uncertainty giảm → dần dần chỉ còn success_rate quyết định.

**Ví dụ tính tay UCB1 (bucket medium, N_total = 15):**

| Variant | attempts \(n_a\) | successes | success_rate \(\bar{x}\) | \(\sqrt{2\ln 15 / n_a}\) | uncertainty (×0.1) | **score** |
|---------|------------------|-----------|---------------------------|---------------------------|---------------------|-----------|
| detailed | 8 | 7 | 0.875 | \(\sqrt{2\times2.708/8} \approx 0.823\) | 0.082 | **0.957** |
| minimal | 3 | 1 | 0.333 | \(\sqrt{2\times2.708/3} \approx 1.343\) | 0.134 | 0.467 |
| conservative | 2 | 1 | 0.500 | \(\sqrt{2\times2.708/2} \approx 1.645\) | 0.165 | 0.665 |
| aggressive | 2 | 0 | 0.000 | \(\sqrt{2\times2.708/2} \approx 1.645\) | 0.165 | 0.165 |

→ `detailed` thắng rõ. Nếu không có uncertainty bonus, `detailed` vẫn thắng, nhưng các variant ít thử vẫn được “thưởng” điểm để có cơ hội được chọn sau này.

#### Giải thích ε-greedy

ε-greedy là cơ chế **đơn giản hơn UCB1** để đảm bảo exploration:

- Với xác suất \(\varepsilon = 0.1\) (10%) → **explore**: chọn ngẫu nhiên 1 trong 4 variant.
- Với xác suất \(1-\varepsilon = 0.9\) (90%) → **exploit**: chọn variant có score UCB1 cao nhất.

**Tại sao dùng cả hai?**
- UCB1 tự động giảm exploration khi đã có đủ dữ liệu.
- ε-greedy đảm bảo **luôn còn 10% cơ hội** thử variant mới, dù UCB1 đã “tin” một cái nào đó.

#### Code thật trong project

```python
# adaptive_components/prompt_selector.py — select_variant()
for variant in self.prompt_variants:
    perf = self.context_performance[prop_range][variant]
    if perf['attempts'] == 0:
        score = 0.5 + random.uniform(0, 0.2)   # chưa thử → điểm trung tính + noise
    else:
        success_rate = perf['successes'] / perf['attempts']
        uncertainty  = sqrt(2 * log(N_total) / perf['attempts'])  # UCB1
        score = success_rate + 0.1 * uncertainty

# ε-greedy: exploration_rate = 0.1
if random() < 0.1:
    selected = random.choice(variants)   # 10% explore
else:
    selected = argmax(score)             # 90% exploit
```

**Update sau mỗi lần generate:**

```python
def update_performance(self, variant, property_count, success, quality_score, generation_time):
    prop_range = self._get_property_range(property_count)
    self.context_performance[prop_range][variant]['attempts'] += 1
    if success:
        self.context_performance[prop_range][variant]['successes'] += 1
```

#### Ví dụ chạy tuần tự (bucket medium)

Giả sử bắt đầu từ 0, sau 12 lần generate:

| Lần | random < 0.1? | Hành động | Variant được chọn | Kết quả | Ghi chú |
|-----|---------------|-----------|-------------------|---------|---------|
| 1 | Không | Exploit (score random) | detailed | success | Lần đầu mọi score ≈ 0.5–0.7 |
| 2 | Không | Exploit | detailed | success | detailed dẫn đầu |
| 3 | **Có** | Explore | aggressive | fail | 10% explore |
| 4 | Không | Exploit | detailed | success | |
| 5 | Không | Exploit | detailed | success | |
| 6 | **Có** | Explore | conservative | success | |
| 7 | Không | Exploit | detailed | success | |
| 8 | Không | Exploit | detailed | fail | detailed vẫn cao nhờ uncertainty giảm chậm |
| 9 | Không | Exploit | detailed | success | |
| 10 | Không | Exploit | detailed | success | |
| 11 | **Có** | Explore | minimal | fail | |
| 12 | Không | Exploit | detailed | success | |

Sau 12 lần, bảng điểm có thể trông như:

| Variant | attempts | successes | success_rate | uncertainty | score |
|---------|----------|-----------|--------------|-------------|-------|
| detailed | 8 | 7 | 0.875 | 0.74 | **0.949** |
| minimal | 1 | 0 | 0.000 | 2.08 | 0.208 |
| conservative | 2 | 1 | 0.500 | 1.47 | 0.647 |
| aggressive | 1 | 0 | 0.000 | 2.08 | 0.208 |

→ 90% thời gian chọn `detailed`, 10% vẫn thử các variant khác. Đây chính là cơ chế **exploit + explore** của C2.

**Tóm tắt nhanh khi bị hỏi:**
- UCB1 = success_rate + bonus cho variant ít thử.
- ε-greedy = 10% random, 90% chọn theo UCB1.
- Khác Thompson Sampling (dùng ở chunk-size): TS sample từ Beta posterior, UCB1 dùng công thức bound.
### 4.2. Chunk size — Thompson Sampling (Beta posterior) — ĐÚNG là TS

```python
# adaptive_components/chunk_size_optimizer.py
chunk_sizes = [10, 15, 20, 25, 30]
# mỗi size = 1 arm, Beta(α, β) model P(success)

def select_chunk_size(self, property_count):
    valid = [s for s in chunk_sizes if s <= property_count]
    samples = {}
    for size in valid:
        theta = np.random.beta(self.alpha[size], self.beta[size])  # sample từ posterior
        if self.attempts[size] < 5:
            theta += exploration_bonus   # warm-up
        samples[size] = theta
    return max(samples, key=samples.get)  # arm có sample cao nhất

def update_reward(self, chunk_size, success, quality_score, generation_time):
    R = (100 if success else 0) + quality*50 - time*0.01
    normalized = clip(R / 150, 0, 1)
    if normalized > 0.5:
        self.alpha[chunk_size] += normalized   # success → α↑
    else:
        self.beta[chunk_size] += 1 - normalized  # fail → β↑
```

→ **Prompt selector = UCB1+ε-greedy**; **chunk size = Thompson Sampling thật**. Study guide cũ từng nói “không phải Beta-posterior” chỉ đúng với **prompt variant**, không đúng với chunk-size optimizer.

### 4.3. Kết quả thực nghiệm

C1 vs C2: Mann-Whitney p=0.903, r=0.012 (**negligible**) — chọn lọc giữa các prompt tĩnh + chunk size adaptive **không** tạo khác biệt đáng kể so với 1 prompt tốt nhất cố định. Lý do hợp lý: 4 variant vẫn cùng “family” instruction, thiếu grounding AOSP (RAG) và thiếu feedback lỗi (C4).

> **Ghi chú phòng vấn:** Thesis PDF gọi C2 là “Thompson Sampling”. Thực tế chỉ **chunk-size** dùng Thompson Sampling; **prompt variant** dùng UCB1 + ε-greedy. Có thể giải thích: “C2 dùng bandit-style adaptive selection (UCB1 cho prompt, Thompson cho chunk size).”

---

## 5. Chunking cho domain lớn

**CHUNK_SIZE giới hạn** — domain > ngưỡng bị chia thành batch không chồng lấp (disjoint), gọi `entries_predictor`/`register_body_predictor` riêng từng batch, merge kết quả cuối.

**Đặc điểm quan trọng:** mỗi chunk là 1 lệnh gọi LLM **độc lập, stateless** — chunk sau KHÔNG có bộ nhớ về chunk trước (DSPy ChainOfThought không maintain conversation history). Tính đúng đắn cross-chunk dựa vào **cấu trúc** (property slice không chồng lấp + mỗi tên được override về đúng giá trị AIDL thật trước khi generate), không dựa vào "LLM nhớ".

**Narrow entries-retry:** sau khi 1 chunk sinh entries, cross-check từng tên với AIDL thật đã parse — nếu có tên không khớp, chỉ retry LẠI những tên đó (không phải cả chunk) — LLM tự sửa qua 1 lệnh gọi tiếp theo.

---

## 6. C4 — Generate → Validate → Retry

```
Generate → Validate (clang + AIDL consistency check) → Pass?
                                                          → yes: ghi file
                                                          → no:  error feedback → prompt mới → Generate lại
```

Error feedback được đưa vào `extra_context`, merge vào `aosp_context` — chảy vào MỌI lệnh gọi `entries_predictor`/`register_body_predictor` của lần retry, cho LLM biết chính xác cần sửa gì thay vì generate lại ngẫu nhiên.

**Giới hạn có chủ đích:** retry có `MAX_RETRIES` cố định — hết ngân sách mà vẫn lỗi, giữ bản điểm cao nhất, đánh dấu chưa đạt, pipeline **tiếp tục** (không dừng cứng toàn bộ run). Trade-off giữa throughput và đảm bảo tuyệt đối.

> **Ghi chú phòng vấn:** Thesis viết “C4 extends C2 and C3”. Code thực tế C4 = **C3 + retry loop** (không chạy bandit prompt của C2). Có thể nói: “C4 kế thừa RAG+DSPy của C3 và thêm feedback loop; bandit C2 không được kích hoạt trong C4.”

---

## 7. Kiến trúc Validation — 2 tầng độc lập

| Tầng | Công cụ | Bắt được | Không bắt được |
|---|---|---|---|
| 1 — Colab | `clang++ -fsyntax-only` + stub AOSP header | Cú pháp, duplicate case, undefined identifier | Lỗi Soong build system, linker, hành vi runtime |
| 2 — GCP Cuttlefish | AOSP 14 build thật + VTS | Mọi thứ tầng 1 bỏ sót | — (ground truth) |

Tầng 1 chạy trên MỌI lần generate/retry (rẻ, nhanh — hàng nghìn lần trong suốt thực nghiệm C1-C4). Tầng 2 chạy 1 lần trên artifact C4 cuối cùng (đắt — cần build thật + boot emulator).

### 7.1. Bên trong tầng 1 — 5 lớp check TRƯỚC KHI gọi clang

`validate_cpp()` không gọi clang ngay — chạy qua 5 "hard-fail check" bằng Python thuần trước, mỗi cái bắt 1 lớp lỗi mà compiler không thấy được (vì compile được không có nghĩa là ĐÚNG):

| # | Check | Bắt lỗi gì | Vì sao clang không tự bắt được |
|---|---|---|---|
| 1 | Đếm `{`/`}` cân bằng | Output bị cắt cụt giữa chừng (hết token) | Code cụt vẫn có thể "trông giống" hợp lệ tới điểm bị cắt |
| 2 | Regex `(*callback)({})` | Stub giả — trả rỗng thay vì đọc/ghi file thật | Đây là hợp lệ về cú pháp, chỉ sai về NGỮ NGHĨA (không làm đúng việc) |
| 3 | Field `booleanValues`/`boolValues` | Dùng field không tồn tại trong `RawPropValues` thật | Field này CÓ TỒN TẠI trong stub tự viết của validator (nếu không chặn riêng) — compile qua nhưng AOSP thật không có |
| 4 | `readRegister`/`writeRegister` khai báo mà không định nghĩa | Link-time error, không phải compile-time | `-fsyntax-only` không chạy tới bước link, nên không tự phát hiện |
| 5 | Đếm `case` trong `readRegister()` so với số property trong `getAllPropertyConfigs()` | Property "chui lọt" qua compile nhưng thiếu hẳn 1 case | Thiếu 1 case = rơi vào `default: return false` — vẫn hợp lệ cú pháp |

Chỉ SAU KHI qua đủ 5 lớp này, code mới thật sự được đưa cho `clang++ -fsyntax-only` compile.

### 7.2. Cơ chế inject property name vào MỌI lỗi clang

Sau khi clang trả lỗi, mỗi dòng lỗi được xử lý thêm 1 bước: dò NGƯỢC từ số dòng lỗi lên trên, tìm block `case static_cast<int32_t>(VehicleProperty::X)` GẦN NHẤT bao quanh dòng đó — gắn tên `X` vào cuối thông báo lỗi.

```python
def _enclosing_property_name(line_no: int) -> str:
    for i in range(line_no - 1, -1, -1):  # dò ngược từ dòng lỗi
        m = name_re.search(code_lines[i])
        if m:
            return m.group(1)
    return ""
```

Đây là cầu nối giữa "validator phát hiện lỗi ở dòng nào" và "cơ chế retry biết chunk nào cần sửa" (mục 7.3).

### 7.3. Surgical retry — regenerate đúng 1 chunk, không phải toàn bộ domain

**Vấn đề kiến trúc:** domain lớn được chia nhiều chunk, mỗi chunk là 1 lệnh gọi LLM độc lập. Regenerate lại TOÀN BỘ khi validate fail có xác suất cao không hội tụ — sửa đúng chunk lỗi cũ không đảm bảo chunk khác (generate lại từ đầu) không tự tạo lỗi MỚI.

```python
# Bước 1: trích tên property từ error feedback (mọi lỗi đều có tên, nhờ mục 7.2)
reported_names = set(re.findall(r"'([A-Z][A-Z0-9_]*)'", extra_context))

# Bước 2: map tên → chunk index
for i in range(0, len(prop_list), chunk_size):
    chunk_idx = i // chunk_size
    chunk_prop_names = {p.name for p in prop_list[i:i+chunk_size]}
    if chunk_prop_names & reported_names:
        chunks_needing_regen.add(chunk_idx)

# Bước 3: chunk KHÔNG lỗi → lấy nguyên case cũ, KHÔNG gọi LLM lại
if chunk_idx not in chunks_needing_regen:
    reused_read, reused_write = _reuse_chunk_cases(chunk)
    continue
```

**Điểm mấu chốt:** `previous_full_code` không bao giờ vào prompt LLM — chỉ dùng để trích xuất (regex + đếm ngoặc, thuần Python) case của chunk KHÔNG lỗi. Prompt mỗi lần retry vẫn nhỏ y hệt trước, không có rủi ro phình token dù `previous_full_code` dài hàng nghìn dòng.

**Trích xuất theo TÊN, không theo VỊ TRÍ:** code cuối cùng là các chunk đã NỐI LẠI (ranh giới chunk không còn tồn tại trong text), nên phải quét toàn bộ tìm case theo TÊN property, không thể cắt theo vị trí ký tự.

### 7.4. Cross-chunk deduplication

Dedup "trong 1 lần gọi" không đủ vì mỗi chunk độc lập — 2 chunk khác nhau có thể ĐỘC LẬP cùng sinh case cho 1 property. Lớp dedup thứ 2 chạy SAU KHI merge toàn bộ chunk — điểm DUY NHẤT có đủ thông tin toàn cục để phát hiện trùng lặp xuyên-chunk.

---

## 8. Runtime Call Chain đã validate

```
VTS Client (IVhalClient::getValueSync/setValueSync)
    → IVehicle (binder service)
    → VssVehicleHardware (Aggregator — route theo propId → domain, dùng mPropDomainIdx)
    → Domain Service (VehicleHalService{Domain} — readRegister/writeRegister)
    → HW Register File (file thật trên vendor partition)
```

7 VTS test, quan trọng nhất là `VssPropertyRoundTrip` — SET→GET qua ĐÚNG domain service + file I/O thật, test DUY NHẤT chạm toàn bộ chain (6 test còn lại chỉ kiểm tra metadata/config, không exercise đường ghi/đọc thật).

### 8.1. Aggregator dispatch — vì sao cần 1 tầng trung gian, không gọi thẳng domain

`VssVehicleHardware` (Aggregator) là **object DUY NHẤT** được đăng ký với `DefaultVehicleHal` (AOSP reference code) — không phải 7 domain service riêng lẻ. Lý do kiến trúc: AIDL `IVehicle` interface chỉ cho phép **1 implementation `IVehicleHardware` duy nhất** trên toàn hệ thống, nhưng 500 property lại thuộc 7 class khác nhau — Aggregator giải quyết mâu thuẫn này bằng pattern "1 mặt tiền, nhiều impl phía sau":

```cpp
// mPropDomainIdx: map propId -> domain index, build 1 lần lúc khởi tạo
StatusCode VssVehicleHardware::dispatchGetValues(int domainIdx, ...) const {
    if (domainIdx == 0) { VehicleHalServiceAdas svc; return svc.getValues(...); }
    else if (domainIdx == 1) { VehicleHalServiceBody svc; return svc.getValues(...); }
    // ... đủ 7 domain
}
```

**Chi phí thật của pattern này:** mỗi lần dispatch, 1 object domain service MỚI được khởi tạo TẠI CHỖ (`VehicleHalServiceAdas svc;` — biến cục bộ, không static/singleton) — nghĩa là domain service KHÔNG giữ trạng thái giữa các lần gọi, mọi state (nếu có) phải nằm trong file thật trên đĩa (`registerPath()` đọc/ghi qua `std::ifstream`/`std::ofstream`), không phải trong memory của object.

### 8.2. `dump()` cũng dùng đúng pattern dispatch này (không phải logic riêng)

`dumpsys --get/--set` đi qua ĐÚNG con đường dispatch trên, chỉ khác ở bước đầu: Aggregator tự resolve tên property (hoặc ID số) thành `propId` bằng `mPropNames` (map tên→ID, build cùng lúc với `mPropIds` từ dữ liệu AIDL thật) — rồi mới tra `mPropDomainIdx` để biết gọi `dispatchDump()` với domainIdx nào.

---

## 9. Phương pháp thống kê (giải thích lại)

### 9.1. "Khác biệt rõ" = cần CẢ 2 điều

- **p < 0.05** → khác biệt **không do ngẫu nhiên** (đáng tin).
- **r đủ lớn** → khác biệt **đủ lớn thực chất** (0.1 small · 0.3 medium · 0.5 large).

→ **C1 vs C4** đạt cả hai (p = 0.000189, r = 0.375) → đây là kết luận chính của thesis. Chỉ đạt 1 trong 2 thì **chưa** rõ.

### 9.2. Vì sao dùng Kruskal / Mann-Whitney (không t-test/ANOVA)

Score bị chặn **[0, 1]**, phân phối **lệch** (dồn gần 1.0) → vi phạm giả định phân phối chuẩn → dùng test theo **hạng** (rank-based):

- **Kruskal-Wallis** — so 4 nhóm cùng lúc: "có cặp nào khác không?" → H = 20.4950, p = 0.000134 → **Có**.
- **Mann-Whitney U** — chạy sau, chỉ ra **cặp nào** khác biệt.

### 9.3. Số liệu thật (3 cặp cần nhớ)

| Cặp | p | r | Đọc sao |
|-----|---|---|---------|
| C1 vs C2 | 0.903 | 0.012 | negligible — adaptive không tạo khác biệt |
| **C1 vs C4** | **0.000189** | **0.375** | **medium + có ý nghĩa — kết luận chính** |
| C3 vs C4 | 0.273 | 0.114 | chưa đạt ngưỡng — feedback chưa đủ mạnh thống kê |

Công thức: `r = 1 − 2U / (n₁·n₂)`. Đơn vị test = **266 điểm module-level** (không phải 500 signal).

### 9.4. ⭐ Coverage KHÔNG nằm trong công thức U/p/r

Test chỉ ăn **1 số mỗi module = composite**. Coverage đã bị gộp vào composite qua `w_cov` **trước khi** test chạy:

```
composite = w_struct·S + w_syntax·X + w_cov·C ← coverage nằm ở đây
Mann-Whitney chỉ thấy 0.8328 vs 0.8917 , KHÔNG thấy C = 0.58 vs 0.80
```

→ Coverage tác động **gián tiếp**: coverage↑ → composite↑ → hạng cao hơn → U↓ → p↓, r↑.

**"Coverage là nguyên nhân" chứng minh bằng phân rã (Table 8), không bằng U/p/r:**

| Chiều | C1 → C4 | |
|-------|---------|---|
| Structure | 0.9556 → 0.9382 | đứng yên (còn giảm nhẹ) |
| Syntax | 0.9067 → 0.9241 | nhích nhẹ |
| **Coverage** | **0.5833 → 0.7996** | **nhảy mạnh** |

Chỉ **coverage** dịch chuyển → coverage là driver, **dù `w_cov` nhỏ nhất**. Lý do: struct/syntax đã bão hòa ~0.90–0.95 (hết dư địa), coverage là chiều duy nhất còn chỗ để tăng. U/p/r chỉ nói "**có** khác biệt ở composite"; phân rã mới cho biết khác biệt **nằm ở coverage**.

### 9.5. Câu chốt phòng vấn

- **Score 0.85 không tự mang ý nghĩa thống kê** — chỉ là rubric; phải có test mới kết luận.
- **Structural score ≠ accuracy** — không có gold label classification.
- **"Cách tính tạo ra khác biệt?"** → Không. Cùng công thức, cùng trọng số, áp **cả 4 điều kiện y hệt**; nếu output giống nhau thì điểm bằng nhau.
- **"Tăng lên 1000 signal thì p nhỏ hơn?"** → Không. N tính theo **module (domain × agent)**, không theo signal → thêm signal chỉ làm file dày hơn, **không** tăng power. Cái cần lo khi scale là token-budget/truncation, không phải mức ý nghĩa.
- **C3 vs C4 chưa sig** → báo cáo trung thực, đừng nói C4 vượt C3 "rõ". Phần lớn khác biệt có ý nghĩa đã đến từ RAG ở C3.

---

# PHẦN B — KIẾN THỨC NỀN AI/LLM

## 1. LLM sinh code như thế nào

LLM (kiến trúc Transformer) dự đoán **token tiếp theo** dựa trên chuỗi token trước đó (autoregressive). Code là text — sinh từng token 1, mỗi bước tính phân phối xác suất trên toàn vocabulary, chọn/sample 1 token, lặp lại tới khi gặp EOS hoặc chạm max_tokens.

**Temperature/top_p** — hệ số điều chỉnh độ "nhọn" của phân phối xác suất trước khi sample: thấp → gần deterministic; cao → đa dạng hơn, rủi ro sai nhiều hơn.

**Giá trị thật dùng trong project** (`llm_client.py`):

```python
def call_llm(
    prompt: str,
    system: str = "",
    *,
    temperature: float = 0.25,   # ← mặc định generate code
    top_p: float = 1.0,
    ...
):
    payload = {
        "model": "qwen2.5-coder:32b",
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "num_ctx": 32768,          # 32K ổn định hơn 128K RoPE scaling
            "num_predict": -1,
        },
    }
```

Riêng lệnh gọi liên quan parse JSON / AIDL agent dùng `temperature=0.0` (deterministic tuyệt đối — JSON cần đúng cú pháp, không cần đa dạng):

```python
# VHALAidlAgent / call_llm_json
raw = call_llm(..., temperature=0.0, response_format="json")
```

**Vì sao 0.25, không phải 0.0 hay 0.7:**
- `0.0` hoàn toàn + cùng prompt → output **y hệt** lần trước → retry C4 vô nghĩa.
- `0.7+` → code dễ lệch cấu trúc, hallucinate identifier.
- `0.25` = đủ thấp để cấu trúc ổn định, đủ cao để retry có cơ hội sinh bản khác khi nhận error feedback.

---

## 2. Metric function vs Loss function

| | Loss function | Metric function |
|---|---|---|
| Dùng khi nào | Training/fine-tune (gradient descent) | Đánh giá output, không đổi trọng số model |
| Đặc điểm | Phải khả vi (differentiable) | Không cần khả vi |
| Ví dụ | Cross-entropy, MSE | Accuracy, structural score |

**Thesis này KHÔNG có loss function** — không fine-tune model, không backpropagation, không gradient. Model (Qwen2.5-Coder) dùng nguyên trọng số (frozen), chỉ thay đổi **PROMPT** — đây là prompt engineering, không phải model training.

MIPROv2 dùng **metric function** (structural score) để tìm kiếm (search) instruction+demo tốt nhất — về bản chất thuật toán gần **Bayesian optimization/black-box search**, không phải gradient-based optimization.

**Trọng số scoring thật** (`dspy_opt/metrics.py` + `rescore_all_conditions.py`):

```
score = w_struct · structural + w_syntax · syntax_valid + w_cov · coverage
```

| Agent | struct | syntax | coverage | Ghi chú |
|---|---|---|---|---|
| aidl | 0.30 | 0.50 | 0.20 | coverage = fraction tên signal VSS xuất hiện trong enum |
| cpp | 0.35 | 0.45 | 0.20 | coverage = fraction property name trong case/read/write |
| selinux | 0.25 | 0.65 | 0.10 | coverage proxy theo domain keyword (tên signal không xuất hiện trong .te) |
| build | 0.35 | 0.55 | 0.10 | tương tự — domain keyword |
| design_doc | 0.50 | 0.30 | 0.20 | |
| android_app | 0.30 | 0.40 | 0.30 | coverage quan trọng hơn vì UI phải map đúng property |

`syntax` luôn có trọng số cao nhất (hoặc gần nhất) — lỗi cú pháp = không compile được = hỏng hoàn toàn. SELinux có `syntax=0.65` (cao nhất bảng) — policy sai cú pháp có thể gây lỗi bảo mật nghiêm trọng.

### Coverage là gì (giải thích lại rõ)

**Coverage ≠ accuracy.** Không so với ground-truth nhãn có sẵn.

```python
# dspy_opt/metrics.py — _signal_coverage
def _signal_coverage(example, code: str) -> float:
    """Fraction of expected VSS property short-names found in generated code.
    e.g. 'VEHICLE_ADAS_ABS_ISENABLED' → looks for 'ISENABLED' in code.
    Returns 1.0 if no ground-truth properties are available.
    """
    props_text = getattr(example, "properties", "") or ""
    names = re.findall(r"Name:\s*\S+_(\w+)", props_text)
    if not names:
        return 1.0
    code_lower = code.lower()
    covered = sum(1 for n in names if n.lower() in code_lower)
    return round(covered / len(names), 4)
```

- **AIDL/CPP:** đếm bao nhiêu % tên property (short-name) từ VSS spec xuất hiện trong output.
- **SELinux/Build:** proxy theo domain keyword (`adas`, `hal_adas`, `vendor.vss.adas`) vì tên signal không xuất hiện trong `.te`/`.bp`.
- Kết quả thực nghiệm: C2→C3 (thêm RAG) coverage **0.590 → 0.792 (+34.3%)** — bằng chứng RAG giảm hallucinate tên property.

---

## 3. RAG — bản chất

LLM có kiến thức "đóng băng" tại thời điểm huấn luyện, dễ hallucinate chi tiết. RAG: tìm đoạn text THẬT liên quan trước khi hỏi, đưa vào prompt — LLM không cần "nhớ", chỉ cần đọc và dùng.

**Cosine similarity** (đo hướng vector, không đo độ dài):

```
sim(A,B) = (A·B) / (‖A‖ × ‖B‖)
```

**Kết quả thực nghiệm nối lại:** C2→C3 (thêm RAG), coverage tăng 0.590→0.792 (+34.3%) — bằng chứng cho đúng lý do RAG tồn tại: context AOSP thật giúp giảm hallucinate tên property.

---

## 4. Prompt Engineering — khái niệm cơ bản

| Kỹ thuật | Ý nghĩa | Trong thesis |
|---|---|---|
| Zero-shot | Chỉ instruction, không ví dụ | C1 baseline gần dạng này |
| Few-shot | Instruction + ví dụ mẫu | DSPy bootstrap demonstrations |
| Chain-of-Thought (CoT) | Yêu cầu "suy nghĩ từng bước" trước khi ra đáp án | `dspy.ChainOfThought` — dùng xuyên suốt toàn pipeline |

**Prompt Engineering vs Fine-tuning:**

| | Prompt Engineering (thesis dùng) | Fine-tuning |
|---|---|---|
| Đụng trọng số model | Không | Có |
| Cần GPU huấn luyện | Không (chỉ inference) | Có |
| Cần gold label | Không bắt buộc | Thường cần |
| Là "học máy" theo nghĩa cổ điển | Không — không có gradient descent | Có |

---

## 5. Câu hỏi cơ bản hay gặp

**"Model có học được gì không, hay chỉ generate?"**  
→ Không — trọng số đứng yên suốt thesis. "Cải thiện" duy nhất ở tầng PROMPT (MIPROv2 search) và retry (feedback trong 1 phiên, không lưu lại).

**"LLM có nhớ prompt cũ không, sao cần RAG?"**  
→ Mỗi lệnh gọi API là request độc lập hoàn toàn (stateless) — không có bộ nhớ giữa các lần gọi.

**"Score 0.85 có ý nghĩa thống kê không?"**  
→ Score là kết quả metric function (rubric), không tự thân mang ý nghĩa thống kê — cần Kruskal-Wallis/Mann-Whitney để biết khác biệt giữa các score có ý nghĩa hay ngẫu nhiên.

**"Structural score có phải accuracy không?"**  
→ Không — accuracy so với ground-truth nhãn có sẵn (classification). Structural score là rubric tự thiết kế (weighted struct/syntax/coverage), không có ground-truth cho bài toán sinh code phức tạp này.

**"Vì sao chọn Qwen2.5-Coder, không phải GPT-4/Claude?"**  
→ ⚠️ Không có lý do ghi lại trong code (`llm_client.py` chỉ có comment `# Perfect choice!`, không giải thích) — **câu này cần tự chuẩn bị câu trả lời thật.** Gợi ý hướng: chạy local qua Ollama (không phụ thuộc rate-limit/chi phí API thương mại), 32B đủ mạnh + chuyên biệt cho code, chạy được trên Colab A100, context 32K đủ cho prompt dài (RAG + contract + properties).

---

## 6. Checklist tự kiểm tra

- [ ] Vẽ lại được pipeline tổng thể, biết chính xác C1-C4 khác nhau ở đâu
- [ ] Giải thích được HIDL filter (2-layer hard trong code / 3-layer trong thesis), vì sao lọc theo path không theo keyword
- [ ] Giải thích được MIPROv2 tối ưu bằng SEARCH (không phải gradient), vì sao gọi vậy
- [ ] Đọc được công thức UCB1 của C2, biết đây không phải Thompson Sampling thật (chỉ chunk-size mới là TS)
- [ ] Giải thích được vì sao chunk độc lập vẫn đúng (cấu trúc, không phải LLM nhớ)
- [ ] Vẽ lại được runtime call chain 5 bước, biết VssPropertyRoundTrip test gì mà 6 test kia không test
- [ ] Tính tay được effect size r từ U, n1, n2
- [ ] Trả lời được: thesis có loss function không, và vì sao không
- [ ] Có câu trả lời thật cho "vì sao chọn Qwen2.5-Coder"
- [ ] Biết C4 = C3 + retry (không chạy bandit C2)
```