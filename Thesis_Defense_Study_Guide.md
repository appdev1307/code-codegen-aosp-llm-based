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
composite = w_struct·S + w_syntax·X + w_cov·C   ← coverage nằm ở đây
Mann-Whitney chỉ thấy  0.8328 vs 0.8917 , KHÔNG thấy  C = 0.58 vs 0.80
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