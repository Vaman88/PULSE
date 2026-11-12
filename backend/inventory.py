"""Account-scoped inventory storage, independent of demand forecasts."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class Medicine(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    name: str = Field(min_length=1, max_length=160)
    strength: str = Field(default='', max_length=80)
    form: str = Field(default='', max_length=80)
    manufacturer: str = Field(default='', max_length=160)
    identifier: str = Field(default='', max_length=100)
    unit: str = Field(min_length=1, max_length=40)
    quantity: float = Field(ge=0, le=1e12, allow_inf_nan=False)
    low_stock: float = Field(default=0, ge=0, le=1e12, allow_inf_nan=False)
    archived: bool = False
    version: int = Field(default=0, ge=0)


def inventory(path: Path, owner: str, medicine: Medicine | None = None, item_id: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=15)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS medicines (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
            strength TEXT NOT NULL, form TEXT NOT NULL, manufacturer TEXT NOT NULL,
            identifier TEXT NOT NULL, unit TEXT NOT NULL, quantity REAL NOT NULL,
            low_stock REAL NOT NULL, archived INTEGER NOT NULL,
            version INTEGER NOT NULL, updated_at TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS medicines_owner ON medicines(owner)')
        if medicine is None:
            return [dict(row) for row in db.execute(
                'SELECT * FROM medicines WHERE owner = ? ORDER BY name COLLATE NOCASE, strength', (owner,))]
        values = medicine.model_dump(exclude={'version'})
        values['updated_at'] = datetime.now(timezone.utc).isoformat()
        if item_id:
            existing = db.execute('SELECT * FROM medicines WHERE id = ? AND owner = ?', (item_id, owner)).fetchone()
            if existing is None:
                raise HTTPException(404, 'Medicine not found')
            if existing['unit'] != medicine.unit:
                raise HTTPException(400, 'Keep this medicine’s inventory unit consistent. Create a separate entry for a different unit.')
            changed = db.execute('UPDATE medicines SET ' + ', '.join(f'{key} = ?' for key in values)
                                 + ', version = version + 1 WHERE id = ? AND owner = ? AND version = ?',
                                 (*values.values(), item_id, owner, medicine.version))
            if changed.rowcount != 1:
                raise HTTPException(409, 'This medicine changed in another session. Reload inventory before editing again.')
        else:
            item_id = str(uuid4())
            db.execute('INSERT INTO medicines (' + ', '.join(values) + ', id, owner, version) VALUES ('
                       + ', '.join('?' for _ in range(len(values) + 3)) + ')', (*values.values(), item_id, owner, 1))
        return dict(db.execute('SELECT * FROM medicines WHERE id = ? AND owner = ?', (item_id, owner)).fetchone())
