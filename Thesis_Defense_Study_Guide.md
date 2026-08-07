# Tài liệu kỹ thuật — LLM-Based Code Generation for AAOS VHAL

**Repo:** https://github.com/appdev1307/code-codegen-aosp-llm-based  
**Target:** Android 14 (API 34) · Qwen2.5-Coder-32B (Ollama)  
*Phần A: Kiến trúc & thuật toán (advanced). Phần B: Kiến thức nền AI/LLM (basic).*

---

# PHẦN A — KIẾN TRÚC & THUẬT TOÁN

## 0. VHAL Property ID — cấu trúc bit 32-bit

```
Property ID (32-bit) = group (0xF0000000) | area (0x0F000000) | type (0x00FF0000) | index (0x0000FFFF)
```

```
┌────────────┬────────────┬────────────┬────────────────┐
│   GROUP    │    AREA    │    TYPE    │     INDEX      │
│  4 bits    │  4 bits    │  8 bits    │    16 bits     │
│ 31 … 28    │ 27 … 24    │ 23 … 16    │   15 … 0       │
└────────────┴────────────┴────────────┴────────────────┘
```

**Giá trị project dùng** (`multi_main_c5.py` / AIDL agent `_aaos_encode`):

```python
VSS_GROUP = 0x20000000   # VehiclePropertyGroup::VENDOR
VSS_AREA  = 0x01000000   # VehicleArea::GLOBAL
VSS_TYPE_BITS = {
    "STRING":  0x00100000,
    "BOOLEAN": 0x00200000,
    "INT32":   0x00400000,
    "INT64":   0x00500000,
    "FLOAT":   0x00600000,
}

def encode_prop_id(raw_index, vss_type):
    if raw_index & 0xF0000000:          # đã là full ID
        return raw_index
    type_bits = VSS_TYPE_BITS.get(vss_type.upper(), 0x00400000)
    return VSS_GROUP | VSS_AREA | type_bits | (raw_index & 0xFFFF)
```

**Ví dụ:** `0x21204010` = `VENDOR | GLOBAL | BOOLEAN | index 0x4010`.

**Điểm hay bị hỏi**

| Câu hỏi | Trả lời ngắn |
|---------|----------------|
| Sao không dùng index trần `0x1000`? | Thiếu group/area/type → VHAL coi ID không hợp lệ, property **âm thầm không đăng ký** |
| Sao type nằm trong ID, không field riêng? | `VehiclePropConfig` không có field type độc lập; framework **mask 8 bit type** từ property ID |
| SYSTEM vs VENDOR group? | Property chuẩn AOSP = `SYSTEM (0x10000000)`; property tự định nghĩa từ VSS = `VENDOR (0x20000000)` |

---

## 0b. AIDL versioning — "strict" interface stability

VHAL là **stable AIDL** (structured AIDL) chạy qua ranh giới system↔vendor, nên AOSP áp một bộ luật versioning nghiêm ngặt. Đây là lý do **không thể "sinh AIDL tùy ý"** — code phải tuân đúng contract ổn định, và cũng là một trong các thách thức chính mà thesis nêu ở §1.2.

### Vì sao gọi là "strict"

```
android.hardware.automotive.vehicle-V3      ← interface có SỐ VERSION
        │
        ├─ @VintfStability      → interface nằm trong Vendor Interface (VINTF),
        │                          bắt buộc backward-compatible qua system↔vendor
        ├─ @Backing(type="int") → kiểu nền của enum là 1 phần của contract,
        │                          KHÔNG được đổi sau khi phát hành
        └─ aidl_api/<iface>/<version>/ + .hash
                 → snapshot API bị "đóng băng"; Soong so khớp .aidl hiện tại với
                   snapshot. Nếu khác mà frozen:true  ⇒  BUILD FAIL
```

**Luật cốt lõi của stable AIDL:**

| Được phép | KHÔNG được (khi đã frozen) |
|-----------|-----------------------------|
| Thêm enum constant / method **mới** ở version mới | Sửa hoặc xóa method, field, enum đã có |
| Nâng version mới (V3 → V4) | Đổi thứ tự field, đổi `@Backing` type |
| Thêm vendor property trong dải VENDOR | Đổi giá trị ID của property đã phát hành |

### Cơ chế đóng băng (freeze)

- Mỗi module `aidl_interface` trong Soong có field `frozen`.
- `frozen: true` → build chạy **api-freeze check**: hash của `.aidl` hiện tại phải trùng snapshot trong `aidl_api/`. Lệch ⇒ lỗi kiểu *"AIDL API change detected / hash mismatch"*.
- `frozen: false` → cho phép thay đổi, bỏ qua freeze check.
- **Project dùng `frozen: false`** vì mục đích nghiên cứu (liên tục sinh property mới). Freeze chỉ cần khi publish một stable vendor API cho bên thứ ba tiêu thụ — không phải mục tiêu của thesis.

### Vì sao đây là thách thức cho LLM sinh code

1. **Phải sinh đúng pattern enum-of-constants** — KHÔNG được sinh `interface IVehicleAdas { getX(); setX(); }` hay `parcelable AbsConfig {...}`. VehicleProperty là **enum**; thêm method/interface = phá stable contract, Soong stability check sẽ fail. Luật này được **ép cứng trong prompt** của AIDL agent (`agents/vhal_aidl_agent.py:59`): *"Generate ONLY a {enum}.aidl enum file — do NOT generate IVehicle.aidl, IVehicleCallback.aidl, or VehiclePropValue.aidl (these already exist in AOSP)"* và *"must be ADDITIVE to the existing AOSP tree, not a replacement"*.
2. **`@VintfStability` + `@Backing(type="int")` bắt buộc có** — thiếu thì Soong coi là *unstable* AIDL và không cho vào VINTF.
3. **Property mới phải nằm dải VENDOR (`0x20000000`)** để *mở rộng* mà không đụng vào enum `SYSTEM` đã đóng băng của AOSP. Nếu chèn vào enum SYSTEM sẽ đổi hash ⇒ fail freeze check, hoặc trùng ID với property chuẩn.
4. **Đồng bộ 3 nơi:** `Android.bp` (khai `stability: "vintf"`, `version`, `frozen`) ↔ **VINTF manifest** (khai đúng version) ↔ **service** (implement đúng version). Lệch version ⇒ HAL **không đăng ký được lúc runtime** dù đã build qua.

> Tier-1 (AIDL parse trên Colab) **không bắt** được các lỗi này — nó chỉ check cú pháp. Chỉ Tier-2 (`aidl --structured --stability vintf` + Soong + VINTF trên Cuttlefish) mới phát hiện lỗi freeze/hash/version. Đây chính là lý do phải có validation 2 tầng (§7).

### Bằng chứng trong repo (đã đối chiếu source)

| Điểm | File · dòng | Nội dung thực tế |
|------|-------------|-------------------|
| Annotation stable | `gen_hal_minimal_c4.py:504-505` | sinh `@VintfStability` + `@Backing(type="int")` |
| Enum-only, additive | `agents/vhal_aidl_agent.py:59,62-64` | *"Generate ONLY … enum"*, *"do NOT generate IVehicle.aidl…"*, *"no HIDL V2_0"*, *"ADDITIVE … not a replacement"* |
| Android.bp stable | `gen_hal_minimal_c4.py:431-446` | `stability: "vintf"`, `frozen: false`, `versions_with_info: [{version:"1"},{version:"2"}]` |
| VINTF fragment | `agents/vhal_service_build_agent.py:183-197` | `vintf_fragments:[…]` + `<hal format="aidl"> … <instance>default</instance>` |

### Điểm hay bị hỏi

| Câu hỏi | Trả lời ngắn |
|---------|----------------|
| "Strict AIDL versioning" nghĩa là gì? | Interface stable có số version + API đóng băng; đã phát hành thì chỉ được *thêm*, không *sửa/xóa* |
| Sao pipeline sinh enum chứ không interface có getter/setter? | VehicleProperty là stable enum; thêm method sẽ phá contract & fail Soong stability check |
| Vì sao property mới đặt ở dải VENDOR? | Để mở rộng mà không sửa enum SYSTEM đã đóng băng (tránh đổi hash / trùng ID chuẩn) |
| `frozen: false` có phải "gian lận" không? | Không — freeze chỉ bắt buộc khi publish stable API; nghiên cứu sinh property liên tục nên để `false` là đúng |
| Tại sao Tier-1 không đủ, phải cần Tier-2? | AIDL parse chỉ check cú pháp; freeze/hash/VINTF-version chỉ Soong (Tier-2) mới kiểm được |

---

## 0c. AIDL enum được LLM sinh thế nào — qua 4 điều kiện (C1–C4)

**Lõi chung mọi điều kiện** (`agents/vhal_aidl_agent.py` — `VHALAidlAgent`): C1–C4 dùng chung phần parse → encode → write; C2/C3/C4 chỉ *bọc thêm* một lớp ngoài.

```
spec module (danh sách property)
      │
      ▼  prompt: package android.hardware.automotive.vehicle,
      │          @VintfStability, @Backing(type="int"),
      │          CHỈ enum (no IVehicle/parcelable), additive, no HIDL
call_llm()  ──►  JSON {"files":[{path, content}]}    (+ vòng repair JSON nội bộ)
      │
      ▼
_parse_properties()  →  encode ID = VENDOR(0x20000000)|GLOBAL|TYPE|index
      │                  (_aaos_encode → 0x212… BOOLEAN / 0x214… INT …)
      ▼
SafeWriter  →  <Enum>.aidl   (vd. VehiclePropertyChassis.aidl)
```

Mấu chốt: **`RAGDSPyAIDLAgent` kế thừa `VHALAidlAgent`** (`agents/rag_dspy_mixin.py:18`) → C3/C4 tái dùng đúng lõi parse/encode/write và **fallback** về baseline nếu DSPy lỗi. Base model **giữ nguyên** Qwen2.5-Coder-32B ở cả 4 điều kiện — khác biệt điểm số **chỉ** do lớp prompt/RAG/feedback.

**LLM đề xuất, encoder chốt ID (cả 4 điều kiện):** LLM chỉ sinh *tên property + annotation + thân enum*; **giá trị ID KHÔNG lấy từ LLM**. Pipeline *đóng dấu* lại ID 32-bit canonical: `_aaos_encode` (`rag_dspy_aidl_agent.py:102,259` — literal *"Force… / override whatever value the LLM emitted"*) cho C3/C4, và `VssGlueAgent._build_full_prop_id` (`vss_glue_agent.py:35-40`, đọc `.aidl` sinh ra làm *single source of truth* qua `_parse_aidl_properties`) cho cả pipeline. Hai hàm mã hoá **giống hệt nhau** → không có ID bịa hay đụng độ ở bất kỳ điều kiện nào. C1 còn có **deterministic fallback**: nếu LLM trả 0 entry → `_write_fallback_vss` viết enum thẳng từ danh sách property (`vhal_aidl_agent.py:191,231`).

### Khác nhau giữa 4 điều kiện

| ĐK | Agent (nguồn) | Thêm gì so với lõi baseline |
|----|---------------|------------------------------|
| **C1** Baseline | `generate_vhal_aidl` (`architect_agent.py:83` → `vhal_aidl_agent.py`, `CHUNK_SIZE=15`) | Prompt tĩnh, `prompt_variant="default"`. **Không** RAG/DSPy/retry. 1 lần generate (có repair JSON nội bộ) |
| **C2** Adaptive | Lõi baseline + `adaptive_components/` | Chọn **chunk-size** cho domain lớn bằng **Beta Thompson Sampling** (`chunk_size_optimizer.py`) và **prompt-variant** bằng **UCB+ε-greedy** (`prompt_selector.py`); outcome (score≥0.8) cập nhật tracker. Vẫn không RAG/DSPy |
| **C3** RAG+DSPy | `RAGDSPyAIDLAgent` (`rag_dspy_aidl_agent.py`, `CHUNK_SIZE=60`) | (1) **Retrieve** ví dụ `.aidl` thật từ ChromaDB `aosp_aidl` → `aosp_context`; (2) chạy **DSPy program đã MIPROv2-optimize** (`dspy_opt/saved`, output field `aidl_code`) có context nhúng vào; fallback baseline nếu DSPy fail |
| **C4** Feedback | `RAGDSPyAIDLAgent` + `PostValidationRetry` | Y hệt C3, **thêm** vòng **Generate→Validate→Retry**: `_validate_aidl` (Python AIDL grammar parser, `validators.py:127`) fail ⇒ nối lỗi vào context, regen, tối đa **`MAX_RETRIES=3`**, giữ bản điểm cao nhất |

**Thang tăng dần:** C1 (prompt tĩnh) → C2 (chọn prompt/chunk thích nghi) → C3 (+ ground bằng AIDL thật + prompt tối ưu) → C4 (+ sửa theo lỗi validator).

### So sánh nhanh C1–C4 (sinh AIDL enum)

| | LLM sinh thân enum | Prompt tĩnh | RAG grounding | DSPy/MIPROv2 | Validator retry | ID canonical + fallback |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| **C1** Baseline | ✅ | ✅ | — | — | — | ✅ |
| **C2** Adaptive | ✅ | ✅ (chọn variant) | — | — | — | ✅ |
| **C3** RAG+DSPy | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| **C4** Feedback | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

→ Cùng LLM (Qwen2.5-Coder-32B) và cùng "enum-only contract" ở cả 4; C1 là mức sàn không có trợ lực, C4 là đầy đủ. LLM luôn sinh thân enum; ID luôn được encoder đóng dấu.

### Vì sao AIDL của C3/C4 đạt điểm tối đa (khớp Table 8/9)

- **C3 nhảy vọt**: retrieve `.aidl` thật + DSPy prompt → model thấy **đúng pattern property/ID** trong tree AOSP thay vì đoán → *coverage* 0.5896 → 0.7920. Per-agent `aidl`: 0.9536 (C1/C2) → **1.0000** (C3).
- **C4 thêm ít cho AIDL**: validator feedback chủ yếu sửa **syntax/C++**; enum AIDL vốn đã đúng từ C3 nên `aidl` giữ **1.0000** ở C4 (mức tăng của C4 dồn vào C++).

### Điểm hay bị hỏi

| Câu hỏi | Trả lời ngắn |
|---------|----------------|
| 4 điều kiện có đổi base model không? | Không — cùng Qwen2.5-Coder-32B; chỉ đổi lớp prompt/RAG/feedback |
| C2 "adaptive" chọn gì cho AIDL? | Chunk-size (Beta TS) + prompt-variant (UCB+ε-greedy); **không** RAG |
| C3 lấy context ở đâu? | ChromaDB collection `aosp_aidl` — ví dụ `.aidl` thật đã lọc HIDL |
| DSPy lỗi ở C3/C4 thì sao? | Fallback về generation baseline của `VHALAidlAgent` |
| C4 retry AIDL bằng gì? | `_validate_aidl` (AIDL grammar parser); fail ⇒ nối lỗi vào prompt, tối đa 3 lần |
| Vì sao C3/C4 đều dùng chung 1 agent? | `RAGDSPyAIDLAgent` **kế thừa** `VHALAidlAgent`; C4 = C3 + vòng retry |

---

## 1. Pipeline tổng thể

### 1.1. Block diagram end-to-end

```
┌──────────────┐    ┌─────────────┐    ┌────────────────┐    ┌─────────────────────────┐
│  VSS Spec    │───▶│  Labelling  │───▶│ Module Planner │───▶│  Per-module generation  │
│  (~500–1571) │    │ domain+type │    │ 500 → ~7 mods  │    │  AIDL / C++ / SELinux / │
└──────────────┘    └─────────────┘    └────────────────┘    │  Build (+ support)      │
                                                             └───────────┬─────────────┘
                                                                         │
                              ┌──────────────────────────────────────────┼────────────────┐
                              │  Prompt layer (chỉ khác nhau giữa C1–C4)  │                │
                              │  C1 static │ C2 bandit │ C3 RAG+DSPy │ C4 + retry       │
                              └──────────────────────────────────────────┼────────────────┘
                                                                         ▼
                                                             ┌─────────────────────────┐
                                                             │ VssGlueAgent (Aggregator)│
                                                             │ VssVehicleHardware.cpp  │
                                                             │ + Android.bp + SELinux  │
                                                             └───────────┬─────────────┘
                                                                         ▼
                                                             ┌─────────────────────────┐
                                                             │ Tier-1 Colab validators │
                                                             │ Tier-2 AOSP + Cuttlefish│
                                                             │ (+ C5: VTS + HMI)       │
                                                             └─────────────────────────┘
```

### 1.2. Script ↔ điều kiện thực nghiệm

| Condition | Entry script | RAG | DSPy/MIPROv2 | Adaptive prompts | Validate→retry |
|-----------|--------------|-----|--------------|------------------|----------------|
| **C1** Baseline | `multi_main.py` | ✗ | ✗ | ✗ | ✗ |
| **C2** Adaptive | `multi_main_adaptive.py` | ✗ | ✗ | ✓ | ✗ |
| **C3** RAG+DSPy | `multi_main_rag_dspy.py` | ✓ | ✓ | ✗ | ✗ |
| **C4** Feedback | `multi_main_c4_feedback.py` | ✓ | ✓ | ✗ | ✓ |
| **C4-Minimal** | `gen_hal_minimal_c4.py` | ✓ | ✓ | ✗ | ✓ (AIDL+CPP+SELinux only) |
| **C5** VTS+HMI | `multi_main_c5.py` | reads C4 output | | | generates VTS + HMI |

**Điểm then chốt khi trả lời hội đồng:** bốn điều kiện C1–C4 **cùng một base model** (Qwen2.5-Coder-32B); chỉ tầng prompt/retrieval/feedback thay đổi → khác biệt score gán được cho prompt engineering, không phải model.

### 1.3. Cây thư mục quan trọng trong repo

```
code-codegen-aosp-llm-based/
├── multi_main*.py              # entry points C1–C5
├── agents/
│   ├── rag_dspy_*.py           # C3/C4 agents
│   ├── rag_dspy_mixin.py       # shared RAG+DSPy logic
│   ├── vss_glue_agent.py       # Aggregator + unified VehicleProperty.h
│   └── *_adaptive.py           # C2 variants
├── rag/                        # ChromaDB indexing + retrieval
├── dspy_opt/                   # compiled programs (program.json)
├── validator/ / validator.py   # Tier-1 scoring
├── adaptive_components/        # C2 bandit state
└── dataset/                    # VSS inputs
```

---

## 2. RAG Retrieval — hybrid 3 kênh + 3-layer HIDL filter

### 2.1. Block diagram RAG

```
                    ┌─────────────────────────────────────┐
                    │  Offline indexing (một lần)         │
                    │  AOSP android-14.0.0_r75            │
                    │  interfaces / sepolicy / Car        │
                    │  CHUNK=400 words, OVERLAP=50        │
                    │  Layer-1 HIDL path drop             │
                    │  → ChromaDB (7 collections, ~24k)   │
                    │    HNSW, cosine space               │
                    └─────────────────┬───────────────────┘
                                      │
     query (agent + domain + props)   │
                    ┌─────────────────▼───────────────────┐
                    │  Online retrieval                   │
                    │  ┌─────────┐ ┌─────────┐            │
                    │  │ Dense   │ │ BM25    │            │
                    │  │ embed   │ │ sparse  │            │
                    │  └────┬────┘ └────┬────┘            │
                    │       └─────┬─────┘                 │
                    │             ▼                       │
                    │       merge candidates              │
                    │             ▼                       │
                    │  Cross-encoder rerank (bge-reranker)│
                    │             ▼                       │
                    │  HIDL path filter (residual drop)   │
                    │             ▼                       │
                    │  top-k (DEFAULT_TOP_K=6)            │
                    │  format // Example i | file | score │
                    └─────────────────┬───────────────────┘
                                      ▼
                               aosp_context → LLM prompt
```

### 2.2. Tham số thật (đã tinh chỉnh)

```python
CHUNK_SIZE_WORDS    = 400
CHUNK_OVERLAP_WORDS = 50
DEFAULT_TOP_K       = 6      # was 3 — C++ signatures need more context
MIN_SCORE_THRESHOLD = 0.25
max_chars_per_chunk = 1500   # was 800 — signatures truncated
RERANKER_MODEL      = "BAAI/bge-reranker-base"
```

> Nguồn (đã đối chiếu): `rag/aosp_retriever.py:43-46` (`DEFAULT_TOP_K`, `MIN_SCORE_THRESHOLD`, embedding, reranker), `:192` (`max_chars_per_chunk`); `rag/aosp_indexer.py:32-33` (`CHUNK_SIZE_WORDS=400`, `OVERLAP=50`), `:92` (`hnsw:space=cosine`); 7 collections trong `COLLECTION_DEFS`.

### 2.3. Ba kênh retrieval — khi nào cái nào thắng

| Kênh | Cơ chế | Bắt được gì | Hạn chế |
|------|--------|-------------|---------|
| **Dense** | `all-MiniLM-L6-v2` + Chroma HNSW cosine | Ngữ nghĩa gần (API tương tự) | Có thể miss exact name |
| **BM25** | TF-IDF-style keyword | Tên hàm/type **chính xác** | Yếu với paraphrase |
| **Cross-encoder** | Encode cặp (query, doc) cùng lúc | Ranking chính xác hơn bi-encoder | Chậm → chỉ rerank top-N |

### 2.4. HIDL filter — path-only, 2 tầng hard-drop

Code hiện tại lọc HIDL **hoàn toàn bằng path** (content-keyword đã tắt "by design"), qua hai tầng — đều là **hard DROP**, không có soft penalty:

```
Index-time   (rag/aosp_indexer.py:47-56)   ── DROP theo path pattern:
               /1.0/ /2.0/ /3.0/ /4.0/  /hidl/ /hidl-generated/
               /vehicle/2.0/  /v2_0/ /v1_0/ /v3_0/ …
               HIDL_CONTENT_KEYWORDS = []   # tắt keyword-filter CHỦ ĐÍCH
Retrieve-time (rag/aosp_retriever.py:237-248, 271-276)
               ── DROP các chunk residual còn path HIDL sót lại
```

**Vì sao lọc path, không keyword?** Keyword dễ false-positive (comment "migrated from HIDL"). Trong tree AOSP, HIDL nằm trọn trong thư mục versioned (`.../2.0/`, `/hidl/`) nên lọc path là *exact & complete* — đúng như comment trong `aosp_indexer.py:38-42`.

**Hậu quả nếu bỏ filter:** LLM học pattern HIDL deprecated → AIDL Android 14 **compile fail**.

> ⚠️ **Lưu ý paper ↔ code (dễ bị hỏi):** Hình 3 trong paper vẽ "3-layer HIDL filter" (gồm 1 lớp reranker penalty ×0.5). Bản **code đã hợp nhất về path-only hard-drop** (index + retrieve) làm cổng chặn *"single, authoritative"* — **không còn nhánh score ×0.5** trong code hiện tại. Nếu hội đồng hỏi: trả lời "thiết kế 3 lớp; code chốt lại path-only vì exact hơn và đủ để chặn 100% HIDL".

---

## 3. DSPy + MIPROv2

### 3.1. Ba khái niệm cốt lõi

```
┌──────────────┐     ┌─────────────────────┐     ┌──────────────────┐
│  Signature   │────▶│  Module             │────▶│  Predictor       │
│  (contract)  │     │  ChainOfThought(Sig)│     │  (compiled state)│
│  in → out    │     │  + call strategy    │     │  instruction +   │
│  + docstring │     │                     │     │  demos from MIPRO│
└──────────────┘     └─────────────────────┘     └──────────────────┘
```

**`dspy.ChainOfThought` vs `dspy.Predict`:** CoT **tự chèn** field ẩn `reasoning` *trước* output fields → ép model viết suy luận trước khi điền field chính (không cần tự viết “think step by step” trong prompt).

### 3.2. Vòng đời 1 lần gọi predictor

```
predictor(domain=X, properties=Y, aosp_context=Z)
   │
   ├─ build prompt = instruction (program.json nếu có, else Signature docstring)
   │               + few-shot demos (nếu MIPROv2 đã chọn)
   │               + input fields hiện tại
   ├─ Ollama / Qwen2.5-Coder
   ├─ JSONAdapter → object đúng shape Signature
   └─ getattr(result, "read_cases", "") or ""   # tránh AttributeError khi thiếu field
```

### 3.3. MIPROv2 — search, không gradient

```
                    ┌────────────────────────────┐
                    │  metric_fn = composite score│
                    │  (struct/syntax/coverage)   │
                    └─────────────┬──────────────┘
                                  │
         ┌────────────────────────▼────────────────────────┐
         │  MIPROv2.compile(module, trainset, auto=medium) │
         │  1) Bootstrap high-scoring demos                │
         │  2) Propose instruction candidates (LLM)        │
         │  3) Bayesian trial search (TPE / Optuna-style)  │
         │  4) Save best (instruction, demo-set)           │
         └────────────────────────┬────────────────────────┘
                                  ▼
                     dspy_opt/saved/*.program.json
```

**Không có gold label code.** Metric là structural score từ validators — black-box search trên không gian prompt.

**Ceiling agents (bỏ full MIPRO):**

```python
MIPRO_SKIP_AGENTS = {"aidl", "design_doc", "selinux", "build"}
# → LabeledFewShot(k=2) thay vì ~36 trial/agent
```

Lý do: zero trial variance — đổi instruction/demo **không đổi điểm** → full search chỉ tốn ngân sách.

**Config thật khi chạy full (vd. cpp):**

```python
optimizer = dspy.MIPROv2(
    metric=metric_fn,
    auto="medium",
    num_threads=1,              # Ollama serialize thật sự
    verbose=False,
)
```

---

## 4. C2 — Adaptive prompt selection

**Công thức thực tế trong code (UCB1-style + ε-greedy):**

```
uncertainty(v) = sqrt(2 · ln(N_total) / n_v)
score(v)       = success_rate(v) + 0.1 · uncertainty(v)

ε-greedy: 10% random explore, 90% argmax score
```

> Nguồn: `adaptive_components/prompt_selector.py:173-185` (`uncertainty = np.sqrt(...)`; `score = success_rate + 0.1*uncertainty`; epsilon-greedy). Beta Thompson Sampling **riêng** dùng cho chọn **chunk-size** ở `adaptive_components/chunk_size_optimizer.py` (đúng ghi chú "TS áp cho chunk-size, không phải prompt-variant").

- `success_rate` theo **bucket** số property (tiny/small/medium/large/xlarge)
- Success threshold composite ≥ 0.8

**Kết quả:** C1 vs C2 không có ý nghĩa thống kê (Mann-Whitney p≈0.90, r≈0.01) → chọn giữa vài prompt tĩnh **không** vượt 1 prompt tốt cố định trong domain này.

**Lưu ý khi trả lời:** paper/thesis có thể gọi “Thompson Sampling”; implementation adaptive thực tế nghiêng UCB1+ε-greedy. Nên nói rõ “Bayesian multi-armed bandit / adaptive selection; code dùng UCB-style score + ε-greedy”.

---

## 5. Chunking domain lớn

```
Domain với N properties
        │
        ▼
┌───────────────────┐
│ split disjoint    │  CHUNK_SIZE: AIDL≈60, CPP≈30
│ batches (no overlap)
└─────────┬─────────┘
          │
    ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
    │ chunk 0   │   │ chunk 1   │   │ chunk k   │   ← mỗi cái = 1 LLM call ĐỘC LẬP
    │ predictor │   │ predictor │   │ predictor │
    └─────┬─────┘   └─────┬─────┘   └─────┬─────┘
          └───────────┬───┴───────────────┘
                      ▼
                 merge + name override về AIDL thật
                      ▼
              cross-chunk dedup (sau merge)
```

**Đúng cross-chunk nhờ cấu trúc, không nhờ “LLM nhớ”:**

- Property slice **không chồng lấp**
- Tên property được **override** về giá trị AIDL đã parse trước generate
- Dedup toàn cục **sau** merge (2 chunk có thể độc lập sinh cùng 1 case)

**Narrow entries-retry:** chỉ retry các property name mismatch, không regenerate cả chunk.

---

## 6. C4 — Generate → Validate → Retry (+ surgical regen)

### 6.1. Vòng lặp tổng

```
        ┌────────────┐
        │  Generate  │
        └─────┬──────┘
              ▼
        ┌────────────┐
        │  Validate  │  clang + AIDL consistency + 5 hard-fail checks
        └─────┬──────┘
              │
         pass?├── yes ──▶ write file
              │
              no
              ▼
        ┌────────────────────┐
        │ error → extra_ctx  │  merge vào aosp_context
        │ MAX_RETRIES limit  │  hết ngân sách → giữ bản điểm cao nhất, pipeline tiếp tục
        └─────────┬──────────┘
                  │
                  └──▶ Generate lại
```

### 6.2. Năm hard-fail check *trước* clang (`_validate_cpp` · `dspy_opt/validators.py:192`)

| # | Check | Bắt gì | Vì sao clang không đủ |
|---|--------|--------|------------------------|
| 1 | Cân bằng `{`/`}` | Output cắt cụt (hết token) | Đoạn cụt vẫn “trông” hợp lệ tới điểm cắt |
| 2 | Regex stub `(*callback)({})` | Stub rỗng thay vì I/O thật | Cú pháp hợp lệ, **sai nghĩa** |
| 3 | Field `booleanValues`/`boolValues` | Field không tồn tại trên AOSP thật | Có thể tồn tại trong stub validator |
| 4 | Khai báo `readRegister`/`writeRegister` không định nghĩa | Lỗi link-time | `-fsyntax-only` không link |
| 5 | Số `case` vs số property configs | Property “chui” compile nhưng thiếu case | `default: return false` vẫn compile |

### 6.3. Surgical retry — chỉ regen chunk lỗi

```
clang error lines
      │
      ▼
_enclosing_property_name(line)  ── dò ngược tìm case VehicleProperty::X
      │
      ▼
reported property names
      │
      ▼
map name → chunk_idx
      │
      ├── chunk lỗi     → gọi LLM lại
      └── chunk sạch    → reuse case cũ từ previous_full_code (regex+brace, không đưa full code vào prompt)
```

**Tại sao không đưa `previous_full_code` vào prompt?** Tránh phình token; prompt retry vẫn nhỏ bằng lần generate đầu.

> Nguồn: `dspy_opt/validators.py:697` (`_enclosing_property_name` — dò ngược error-line → property); `agents/rag_dspy_cpp_agent.py:711-758` (`chunks_needing_regen`, `_reuse_chunk_cases` — chỉ regen chunk lỗi, copy nguyên chunk sạch); `MAX_RETRIES = 3` ở `multi_main_c4_feedback.py:68`.

---

## 7. Validation 2 tầng

```
┌──────────────────────────────────────────────────────────┐
│ Tier-1  Colab (mọi generate/retry — rẻ, hàng nghìn lần)  │
│  · Python structure/coverage heuristics                  │
│  · clang++ -fsyntax-only (+ stub headers)                │
│  · checkpolicy / AIDL parse / BP parse                   │
│  Bắt: syntax, duplicate case, undeclared id              │
│  Không bắt: Soong, linker, runtime behaviour             │
└───────────────────────────┬──────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────┐
│ Tier-2  GCP + Cuttlefish (ít lần — đắt)                  │
│  · AOSP 14 tree, aosp_cf_x86_64_auto                     │
│  · aidl --structured, mmm/Soong, full image, VTS         │
│  Ground truth production orientation                     │
└──────────────────────────────────────────────────────────┘
```

**Composite score (Tier-1):**

```
Score(a) = w_s·S(a) + w_x·X(a) + w_c·C(a)
```

| Agent | struct | syntax | coverage |
|-------|--------|--------|----------|
| aidl | 0.30 | 0.50 | 0.20 |
| cpp | 0.35 | 0.45 | 0.20 |
| selinux | 0.25 | **0.65** | 0.10 |
| build | 0.35 | 0.55 | 0.10 |
| design_doc | 0.50 | 0.30 | 0.20 |

Syntax trọng số cao vì fail cú pháp = không vào được build. SELinux syntax=0.65 vì policy sai có thể phá toàn bộ image.

---

## 8. Runtime call chain (đã validate trên Cuttlefish)

```
VTS Client
  getValueSync / setValueSync
        │
        ▼
IVehicle  (binder — DefaultVehicleHal, AOSP-owned)
        │
        ▼
VssVehicleHardware : IVehicleHardware     ← generated Aggregator (vendor seam)
  mPropDomainIdx[propId] → domainIdx
        │
        ▼
VehicleHalService{Domain}   (Adas/Body/Cabin/… — object local mỗi dispatch)
  readRegister / writeRegister
        │
        ▼
HW register file  (vendor partition, ifstream/ofstream)
```

**Vì sao cần Aggregator?** `IVehicle` chỉ cho **một** `IVehicleHardware` implementation; 500 property thuộc 7 class → pattern “1 façade, nhiều domain phía sau”.

**Chi phí:** mỗi dispatch tạo domain service **local** (không singleton) → không giữ state in-memory; state nằm trên file.

**VssPropertyRoundTrip:** SET→GET qua đúng domain + file I/O — test duy nhất exercise full chain (các VTS khác chủ yếu metadata/config).

---

## 9. Thống kê

**Kruskal-Wallis** (4 nhóm, non-parametric):

\[
H = \frac{12}{N(N+1)}\sum_i\frac{R_i^2}{n_i} - 3(N+1)
\]

Ví dụ reported: \(H=20.495\), \(p=0.000134\) → bác bỏ “4 điều kiện cùng phân phối”.

**Mann-Whitney U + effect size** (rank-biserial):

\[
r = 1 - \frac{2U}{n_1 n_2}
\quad\text{(hoặc } r = Z/\sqrt{N}\text{ tùy công thức paper)}
\]

| Cặp | Ý nghĩa thực nghiệm |
|-----|---------------------|
| C1 vs C2 | negligible — adaptive prompts không đủ |
| C1 vs C3 | retrieval đã có tín hiệu |
| C1 vs C4 | medium, significant — full pipeline |
| C3 vs C4 | small, thường chưa vượt ngưỡng mạnh |

**Vì sao non-parametric?** Score bị chặn [0,1], phân phối lệch → không giả định chuẩn như ANOVA/t-test.

---

# PHẦN B — KIẾN THỨC NỀN AI/LLM

## 1. LLM sinh code

Autoregressive Transformer: dự đoán token tiếp theo.  
Project defaults (`llm_client.py`):

```python
temperature = 0.25   # code: ổn định nhưng retry vẫn có cơ hội khác
top_p = 1.0
num_ctx = 32768      # 32K context
# JSON parse paths: temperature = 0.0
```

**0.25 không 0.0:** temperature 0 → retry cùng prompt ra **y hệt** → feedback loop vô nghĩa.

## 2. Metric vs Loss

| | Loss | Metric (thesis) |
|--|------|-----------------|
| Gradient? | Cần differentiable | Không |
| Đổi trọng số model? | Có | **Không** — model frozen |
| Ví dụ | Cross-entropy | struct/syntax/coverage score |

Thesis = **prompt engineering + search**, không fine-tune, không backprop.

## 3. RAG — bản chất một câu

Kiến thức model đóng băng → dễ hallucinate API AOSP.  
RAG đưa **đoạn source thật** vào prompt → model đọc và bắt chước, không cần “nhớ”.

Coverage C2→C3 tăng mạnh (~0.59→0.79) là bằng chứng thực nghiệm cho đúng lý do tồn tại RAG.

## 4. Prompt techniques trong thesis

| Kỹ thuật | Vai trò |
|----------|---------|
| Zero/few-shot | C1 / DSPy demos |
| Chain-of-Thought | `dspy.ChainOfThought` xuyên pipeline |
| Adaptive selection | C2 bandit |
| RAG context | C3/C4 |
| Validator feedback | C4 retry |

## 5. Câu hỏi hội đồng thường gặp

**Model có “học” trong thesis không?**  
Không đổi trọng số. Cải thiện chỉ ở prompt (MIPROv2) và retry trong phiên.

**Vì sao cần RAG nếu model đã biết Android?**  
Mỗi API call **stateless**. Không có bộ nhớ giữa các lần gọi; chi tiết AIDL/SELinux version-pinned không đáng tin nếu chỉ dựa pretrain.

**Score 0.89 có phải accuracy không?**  
Không — không có gold code label. Đây là **rubric** tự thiết kế + build pass/fail.

**Vì sao Qwen2.5-Coder-32B?**  
Gợi ý trả lời: chạy local Ollama (không rate-limit/API cost), chuyên code, 32B đủ mạnh trên Colab A100, context 32K đủ RAG+contract+properties, kiểm soát reproducibility.

**Vì sao SELinux không cải thiện?**  
Macro/dependency nằm ngoài file sinh ra; validator local không resolve đủ sepolicy tree → feedback kém actionable hơn AIDL/clang.

**Surgical retry khác regenerate full domain thế nào?**  
Chỉ LLM lại chunk chứa property lỗi; chunk sạch reuse case đã parse — giảm tạo lỗi mới ở chunk vốn đúng.

---

## 6. Checklist tự kiểm trước defense

- [ ] Vẽ pipeline C1–C4 và chỉ đúng chỗ khác nhau  
- [ ] Giải thích 32-bit property ID + ví dụ encode  
- [ ] 3 kênh RAG + 3 lớp HIDL filter  
- [ ] Signature / Module / Predictor + CoT field `reasoning`  
- [ ] MIPROv2 = search trên metric, không gradient; ceiling agents  
- [ ] Chunk độc lập vẫn đúng nhờ disjoint slice + post-merge dedup  
- [ ] 5 hard-fail trước clang + surgical retry  
- [ ] Aggregator vs domain services + VssPropertyRoundTrip  
- [ ] Kruskal-Wallis / Mann-Whitney / effect size đọc kết hợp  
- [ ] Thesis không có loss function — vì sao  

---

## 7. Sơ đồ một trang “elevator”

```
VSS ──▶ Label ──▶ Modules ──▶ [Prompt layer: C1|C2|C3|C4] ──▶ Artifacts
                                      │                │
                                   RAG│DSPy          Retry│Validate
                                      │                │
                                      └──── encode 32-bit IDs ────┘
                                                    │
                                         VssVehicleHardware (1× IVehicleHardware)
                                                    │
                                         DefaultVehicleHal (AOSP)
                                                    │
                                         Cuttlefish + VTS round-trip
```

**Message 30 giây:** Từ tín hiệu VSS, pipeline LLM sinh multi-artifact VHAL; RAG+feedback cho score cao hơn baseline có ý nghĩa thống kê; AIDL C4 compile và đăng ký trong Android 14 AOSP thật.