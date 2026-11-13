import asyncpg
import os
from datetime import datetime

DATABASE_URL = os.getenv("postgresql://neondb_owner:********@ep-cool-lab-batuu9sm.c-8.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")

async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS shifts (
            id SERIAL PRIMARY KEY,
            shift_date DATE NOT NULL,
            admin_info TEXT,
            next_admin_info TEXT,
            cash_start NUMERIC(10, 2),
            cls_total NUMERIC(10, 2),
            cash_income NUMERIC(10, 2),
            card_income NUMERIC(10, 2),
            sbp_income NUMERIC(10, 2),
            transfers NUMERIC(10, 2),
            pc_ps_topup NUMERIC(10, 2),
            bar_income NUMERIC(10, 2),
            refunds TEXT,
            cash_end NUMERIC(10, 2),
            bonuses_info TEXT,
            bonuses_count INTEGER,
            pc_issues TEXT,
            breakdowns TEXT,
            conflicts TEXT,
            bar_ending TEXT,
            completed_tasks TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    await conn.close()

async def save_shift(data: dict):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute('''
        INSERT INTO shifts (
            shift_date, admin_info, next_admin_info, cash_start, cls_total,
            cash_income, card_income, sbp_income, transfers, pc_ps_topup,
            bar_income, refunds, cash_end, bonuses_info, bonuses_count,
            pc_issues, breakdowns, conflicts, bar_ending, completed_tasks
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
    ''',
        datetime.now().date(),
        data['admin_info'], data['next_admin_info'], data['cash_start'], data['cls_total'],
        data['cash_income'], data['card_income'], data['sbp_income'], data['transfers'],
        data['pc_ps_topup'], data['bar_income'], data['refunds'], data['cash_end'],
        data['bonuses_info'], data['bonuses_count'], data['pc_issues'], data['breakdowns'],
        data['conflicts'], data['bar_ending'], data['completed_tasks']
    )
    await conn.close()

async def get_shifts_by_date(target_date: str):
    conn = await asyncpg.connect(DATABASE_URL)
    date_obj = datetime.strptime(target_date, "%d.%m.%Y").date()
    rows = await conn.fetch('SELECT * FROM shifts WHERE shift_date = $1 ORDER BY id ASC', date_obj)
    await conn.close()
    return rows