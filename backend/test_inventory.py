"""Inventory persistence, ownership, validation, and concurrent-edit contracts."""
from fastapi.testclient import TestClient
import pytest

from backend import api


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'ACCOUNTS', tmp_path / 'accounts.json')
    monkeypatch.setattr(api, 'INVENTORY', tmp_path / 'inventory.sqlite3')
    return TestClient(api.app)


def sign_in(client, username):
    result = client.post('/api/v1/auth/signup', json={'username': username, 'password': 'inventory-example-password'})
    assert result.status_code == 200


def test_inventory_is_private_persistent_and_independent_of_forecast(client, monkeypatch):
    monkeypatch.setattr(api, 'forecast', lambda *args: pytest.fail('Inventory must not run the forecast'))
    assert client.get('/api/v1/inventory').status_code == 401
    payload = {'name': 'Example medicine', 'strength': '10 mg', 'unit': 'bottles', 'quantity': 12, 'low_stock': 3}
    assert client.post('/api/v1/inventory', json=payload).status_code == 401
    sign_in(client, 'pharmacy_a')
    created = client.post('/api/v1/inventory', json=payload)
    assert created.status_code == 201
    item = created.json()
    editable = {key: item[key] for key in payload} | {'version': item['version']}
    assert client.put('/api/v1/inventory/'+item['id'], json=editable | {'unit': 'boxes'}).status_code == 400
    assert client.put('/api/v1/inventory/'+item['id'], json=editable | {'quantity': 15}).status_code == 200
    assert client.put('/api/v1/inventory/'+item['id'], json=editable | {'quantity': 8}).status_code == 409
    assert client.get('/api/v1/inventory').json()['items'][0]['quantity'] == 15
    assert client.post('/api/v1/logout').status_code == 200
    assert client.get('/api/v1/inventory').status_code == 401
    sign_in(client, 'pharmacy_b')
    assert client.get('/api/v1/inventory').json() == {'items': []}
    assert client.put('/api/v1/inventory/'+item['id'], json=editable).status_code == 404
    assert client.post('/api/v1/logout').status_code == 200
    assert client.post('/api/v1/auth/login', json={'username':'pharmacy_a','password':'inventory-example-password'}).status_code == 200
    saved = client.get('/api/v1/inventory').json()['items'][0]
    assert saved['quantity'] == 15
    editable['version'] = saved['version']
    assert client.put('/api/v1/inventory/'+item['id'], json=editable | {'archived': True}).status_code == 200
    archived = client.get('/api/v1/inventory').json()['items'][0]
    assert archived['archived'] == 1
    assert client.put('/api/v1/inventory/'+item['id'], json=editable | {'version':archived['version'], 'archived':False}).status_code == 200


@pytest.mark.parametrize('change', [{'name':'   '}, {'unit':''}, {'quantity':-1}, {'quantity':'NaN'}, {'quantity':1e15}, {'low_stock':-1}, {'unexpected':'value'}])
def test_invalid_entries_do_not_change_inventory(client, change):
    sign_in(client, 'pharmacy_a')
    response = client.post('/api/v1/inventory', json={'name':'Medicine','unit':'bottles','quantity':2} | change)
    assert response.status_code == 422
    assert client.get('/api/v1/inventory').json()['items'] == []
