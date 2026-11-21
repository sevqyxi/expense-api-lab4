"""Інтеграційні тести рольової моделі (RBAC) і варіанта 2 (персональні та глобальні категорії)."""


def create_global_category(client, admin, name="Food"):
    response = client.post("/category/global", headers=admin["headers"], json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def create_personal_category(client, owner, name="Hobby"):
    response = client.post("/category", headers=owner["headers"], json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def create_record(client, owner, category_id, amount=100):
    response = client.post("/record", headers=owner["headers"], json={"category_id": category_id, "amount": amount})
    assert response.status_code == 201
    return response.json()["id"]


# user

def test_user_can_view_own_profile(client, user):
    response = client.get(f"/user/{user['id']}", headers=user["headers"])
    assert response.status_code == 200
    assert response.json()["role"] == "user"


def test_user_cannot_view_other_profile(client, user, other_user):
    response = client.get(f"/user/{other_user['id']}", headers=user["headers"])
    assert response.status_code == 404


def test_user_cannot_use_admin_routes(client, user, other_user):
    assert client.get("/users", headers=user["headers"]).status_code == 403
    assert client.delete(f"/user/{other_user['id']}", headers=user["headers"]).status_code == 403
    response = client.patch(f"/user/{user['id']}/role", headers=user["headers"], json={"role": "admin"})
    assert response.status_code == 403


def test_rbac_forbidden_access(client, user_jwt_header):
    # звичайний user намагається викликати admin-маршрут
    response = client.post("/category/global", headers=user_jwt_header, json={"name": "Global"})
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_user_creates_and_reads_own_record(client, user, admin):
    category_id = create_global_category(client, admin)
    response = client.post("/record", headers=user["headers"], json={"category_id": category_id, "amount": "250.50"})
    assert response.status_code == 201
    record = response.json()
    # власник визначається з JWT
    assert record["user_id"] == user["id"]
    assert client.get(f"/record/{record['id']}", headers=user["headers"]).status_code == 200


def test_user_cannot_spoof_user_id(client, user, other_user, admin):
    category_id = create_global_category(client, admin)
    response = client.post(
        "/record", headers=user["headers"], json={"user_id": other_user["id"], "category_id": category_id, "amount": 10}
    )
    assert response.status_code == 404
    response = client.post("/category", headers=user["headers"], json={"name": "X", "user_id": other_user["id"]})
    assert response.status_code == 404


def test_user_cannot_access_other_users_record(client, user, other_user, admin):
    category_id = create_global_category(client, admin)
    record_id = create_record(client, other_user, category_id)
    assert client.get(f"/record/{record_id}", headers=user["headers"]).status_code == 404
    assert client.delete(f"/record/{record_id}", headers=user["headers"]).status_code == 404
    # запис не видалено
    assert client.get(f"/record/{record_id}", headers=other_user["headers"]).status_code == 200


def test_user_filters_only_own_records(client, user, other_user, admin):
    category_id = create_global_category(client, admin)
    own_id = create_record(client, user, category_id)
    create_record(client, other_user, category_id)
    response = client.get(f"/record?category_id={category_id}", headers=user["headers"])
    assert response.status_code == 200
    assert [r["id"] for r in response.json()] == [own_id]
    assert client.get(f"/record?user_id={other_user['id']}", headers=user["headers"]).status_code == 404


def test_user_can_delete_own_record(client, user, admin):
    record_id = create_record(client, user, create_global_category(client, admin))
    assert client.delete(f"/record/{record_id}", headers=user["headers"]).status_code == 204
    assert client.get(f"/record/{record_id}", headers=user["headers"]).status_code == 404


def test_filter_without_params_returns_400(client, user):
    response = client.get("/record", headers=user["headers"])
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


# категорії (варіант 2)

def test_user_sees_global_and_own_categories_only(client, user, other_user, admin):
    global_id = create_global_category(client, admin)
    own_id = create_personal_category(client, user)
    other_id = create_personal_category(client, other_user)
    response = client.get("/category", headers=user["headers"])
    ids = [c["id"] for c in response.json()]
    assert global_id in ids and own_id in ids and other_id not in ids
    assert client.get(f"/category/{other_id}", headers=user["headers"]).status_code == 404


def test_personal_category_belongs_to_creator(client, user):
    response = client.post("/category", headers=user["headers"], json={"name": "Hobby"})
    assert response.status_code == 201
    assert response.json()["user_id"] == user["id"]


def test_user_cannot_use_other_users_personal_category(client, user, other_user):
    other_id = create_personal_category(client, other_user)
    response = client.post("/record", headers=user["headers"], json={"category_id": other_id, "amount": 10})
    assert response.status_code == 404


def test_user_can_use_own_personal_category(client, user):
    own_id = create_personal_category(client, user)
    response = client.post("/record", headers=user["headers"], json={"category_id": own_id, "amount": 10})
    assert response.status_code == 201


def test_user_cannot_delete_global_category(client, user, admin):
    global_id = create_global_category(client, admin)
    assert client.delete(f"/category/{global_id}", headers=user["headers"]).status_code == 403


def test_user_can_delete_own_personal_category(client, user):
    own_id = create_personal_category(client, user)
    assert client.delete(f"/category/{own_id}", headers=user["headers"]).status_code == 204


def test_admin_cannot_mix_category_of_another_owner(client, user, other_user, admin):
    other_id = create_personal_category(client, other_user)
    response = client.post(
        "/record", headers=admin["headers"], json={"user_id": user["id"], "category_id": other_id, "amount": 10}
    )
    assert response.status_code == 403


# auditor

def test_auditor_can_read_everything(client, user, auditor, admin):
    category_id = create_global_category(client, admin)
    personal_id = create_personal_category(client, user)
    record_id = create_record(client, user, category_id)
    assert client.get("/users", headers=auditor["headers"]).status_code == 200
    assert client.get(f"/user/{user['id']}", headers=auditor["headers"]).status_code == 200
    assert client.get(f"/record/{record_id}", headers=auditor["headers"]).status_code == 200
    assert client.get(f"/category/{personal_id}", headers=auditor["headers"]).status_code == 200
    response = client.get(f"/record?user_id={user['id']}", headers=auditor["headers"])
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_auditor_cannot_write(client, user, auditor, admin):
    category_id = create_global_category(client, admin)
    record_id = create_record(client, user, category_id)
    headers = auditor["headers"]
    assert client.post("/record", headers=headers, json={"category_id": category_id, "amount": 10}).status_code == 403
    assert client.delete(f"/record/{record_id}", headers=headers).status_code == 403
    assert client.post("/category", headers=headers, json={"name": "X"}).status_code == 403
    assert client.post("/category/global", headers=headers, json={"name": "X"}).status_code == 403
    assert client.delete(f"/category/{category_id}", headers=headers).status_code == 403
    assert client.delete(f"/user/{user['id']}", headers=headers).status_code == 403
    assert client.patch(f"/user/{user['id']}/role", headers=headers, json={"role": "admin"}).status_code == 403


# admin

def test_admin_has_full_access(client, user, admin):
    category_id = create_global_category(client, admin)
    response = client.post(
        "/record", headers=admin["headers"], json={"user_id": user["id"], "category_id": category_id, "amount": 10}
    )
    assert response.status_code == 201
    record_id = response.json()["id"]
    assert client.get("/users", headers=admin["headers"]).status_code == 200
    assert client.delete(f"/record/{record_id}", headers=admin["headers"]).status_code == 204
    assert client.delete(f"/category/{category_id}", headers=admin["headers"]).status_code == 204
    assert client.delete(f"/user/{user['id']}", headers=admin["headers"]).status_code == 204


def test_admin_changes_role(client, user, admin):
    response = client.patch(f"/user/{user['id']}/role", headers=admin["headers"], json={"role": "auditor"})
    assert response.status_code == 200
    assert response.json()["role"] == "auditor"
    # нова роль діє одразу
    assert client.get("/users", headers=user["headers"]).status_code == 200


def test_admin_cannot_set_unknown_role(client, user, admin):
    response = client.patch(f"/user/{user['id']}/role", headers=admin["headers"], json={"role": "superuser"})
    assert response.status_code == 400
