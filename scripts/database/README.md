# Database Init Scripts

Raw SQL, tách theo domain. Tất cả idempotent — chạy lại nhiều lần an toàn.

## Thứ tự chạy (bắt buộc)

```
01_users.sql     →  02_catalog.sql  →  03_training.sql
```

- `01` tạo extension `citext`, enum của user, và 3 bảng user. Không phụ thuộc bảng nào.
- `02` tạo master data (muscle_groups, exercises, exercise_muscles) + trigger leaf-only. Độc lập với `01`, nhưng chạy trước `03`.
- `03` tạo workout logging. FK tới `users` (từ 01) và `exercises` (từ 02) — phải chạy sau cùng.

## Chạy tay

```bash
psql "$DATABASE_URL" -f 01_users.sql
psql "$DATABASE_URL" -f 02_catalog.sql
psql "$DATABASE_URL" -f 03_training.sql
```

Hoặc một lệnh:

```bash
for f in 01_users.sql 02_catalog.sql 03_training.sql; do
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

`ON_ERROR_STOP=1` dừng ngay khi có lỗi thay vì chạy tiếp file sau.

## k3s init container

Mount 3 file vào `/docker-entrypoint-initdb.d/` (Postgres official image tự chạy theo thứ tự tên file khi container khởi tạo lần đầu). Prefix số đảm bảo đúng thứ tự.

Lưu ý: `docker-entrypoint-initdb.d` chỉ chạy khi data directory rỗng. Với volume đã có dữ liệu, phải chạy tay hoặc dùng migration tool.

## Không bao gồm

- **Seed data** (muscle_groups, exercises) — file riêng, chạy qua service layer để validate ≥1 primary muscle. Init script chỉ tạo cấu trúc, không đổ dữ liệu.
- **Ràng buộc ≥1 primary muscle** — enforce ở application/seed layer, không ở DB (xem comment trong `02_catalog.sql`).
- `updated_at` tự động — chưa có trigger; xử lý ở SQLAlchemy `onupdate=func.now()` hoặc thêm trigger sau.

## Idempotency

- Bảng: `CREATE TABLE IF NOT EXISTS`
- Index: `CREATE INDEX IF NOT EXISTS`
- Enum: bọc `DO $$ ... EXCEPTION WHEN duplicate_object $$`
- Trigger: `DROP TRIGGER IF EXISTS` trước `CREATE`
- Function: `CREATE OR REPLACE`

Đổi schema đã tồn tại (thêm/xóa cột) thì các script này **không** tự migrate — cần Alembic hoặc `ALTER` tay. Init script chỉ dựng mới.