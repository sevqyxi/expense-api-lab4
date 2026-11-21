from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import database_is_available, get_db, run_migrations
from errors import ApiError
from models import ROLE_ADMIN, ROLE_AUDITOR, ROLE_USER, CategoryModel, RecordModel, UserModel
from schemas import (
    MAX_ID,
    CategoryCreate,
    CategoryOut,
    GlobalCategoryCreate,
    LoginIn,
    RecordCreate,
    RecordOut,
    RegisterIn,
    RoleUpdate,
    TokenOut,
    UserOut,
)
from security import (
    create_access_token,
    get_current_user,
    get_jwt_secret,
    get_token_lifetime,
    hash_password,
    require_roles,
    verify_password,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_jwt_secret()  # без JWT_SECRET_KEY застосунок не запуститься
    run_migrations()
    yield


app = FastAPI(title="Expense API", lifespan=lifespan)

# залежності для перевірки ролей
any_role = Depends(get_current_user)
readers = Depends(require_roles(ROLE_AUDITOR, ROLE_ADMIN))
writers = Depends(require_roles(ROLE_USER, ROLE_ADMIN))
admin_only = Depends(require_roles(ROLE_ADMIN))


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "message": exc.message},
        headers=exc.headers,
    )


# Некоректні дані (тіло, query, path, невалідний JSON) -> 400 замість стандартного 422
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    parts = []
    for err in exc.errors():
        field = ".".join(str(x) for x in err["loc"] if x not in ("body", "query", "path"))
        parts.append(f"{field}: {err['msg']}" if field else err["msg"])
    return JSONResponse(status_code=400, content={"error": "validation_error", "message": "; ".join(parts)})


def not_found(name: str, obj_id) -> ApiError:
    return ApiError(404, "not_found", f"{name} with id {obj_id} not found")


def get_or_404(db: Session, model, obj_id: int, name: str):
    obj = db.get(model, obj_id) if 0 < obj_id <= MAX_ID else None
    if obj is None:
        raise not_found(name, obj_id)
    return obj


def is_user(current: UserModel) -> bool:
    return current.role == ROLE_USER


def check_own_user_id(current: UserModel, user_id: Optional[int]):
    # звичайний user не може підставити чужий user_id: для нього чужих даних "не існує"
    if is_user(current) and user_id is not None and user_id != current.id:
        raise not_found("User", user_id)


def get_visible_category(db: Session, current: UserModel, category_id: int) -> CategoryModel:
    category = get_or_404(db, CategoryModel, category_id, "Category")
    # user бачить лише глобальні та власні персональні категорії
    if is_user(current) and category.user_id is not None and category.user_id != current.id:
        raise not_found("Category", category_id)
    return category


def get_visible_record(db: Session, current: UserModel, record_id: int) -> RecordModel:
    record = get_or_404(db, RecordModel, record_id, "Record")
    if is_user(current) and record.user_id != current.id:
        raise not_found("Record", record_id)
    return record


# Системні маршрути (публічні)

@app.get("/healthz/live")
def healthz_live():
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/healthz/ready")
def healthz_ready():
    if not database_is_available():
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unavailable"})
    return {"status": "ready", "database": "ok"}


# Реєстрація та вхід (публічні)

@app.post("/register", response_model=UserOut, status_code=201)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    if db.scalar(select(UserModel).where(UserModel.username == data.username)):
        raise ApiError(409, "conflict", f"Username '{data.username}' is already taken")
    # нова реєстрація завжди отримує роль user
    user = UserModel(
        username=data.username,
        name=data.name or data.username,
        password_hash=hash_password(data.password),
        role=ROLE_USER,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "conflict", f"Username '{data.username}' is already taken")
    db.refresh(user)
    return user


@app.post("/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(UserModel).where(UserModel.username == data.username))
    if user is None or not verify_password(data.password, user.password_hash):
        raise ApiError(401, "unauthorized", "Invalid username or password")
    return TokenOut(
        access_token=create_access_token(user),
        expires_in=int(get_token_lifetime().total_seconds()),
    )


# Users

@app.get("/users", response_model=List[UserOut])
def get_users(current: UserModel = readers, db: Session = Depends(get_db)):
    return db.scalars(select(UserModel).order_by(UserModel.id)).all()


@app.get("/user/{user_id}", response_model=UserOut)
def get_user(user_id: int, current: UserModel = any_role, db: Session = Depends(get_db)):
    check_own_user_id(current, user_id)
    return get_or_404(db, UserModel, user_id, "User")


@app.delete("/user/{user_id}", status_code=204)
def delete_user(user_id: int, current: UserModel = admin_only, db: Session = Depends(get_db)):
    user = get_or_404(db, UserModel, user_id, "User")
    # записи та персональні категорії видаляє БД (ON DELETE CASCADE)
    db.delete(user)
    db.commit()
    return Response(status_code=204)


@app.patch("/user/{user_id}/role", response_model=UserOut)
def change_user_role(
    user_id: int, data: RoleUpdate, current: UserModel = admin_only, db: Session = Depends(get_db)
):
    user = get_or_404(db, UserModel, user_id, "User")
    user.role = data.role
    db.commit()
    db.refresh(user)
    return user


# Categories

@app.get("/category", response_model=List[CategoryOut])
def get_categories(
    user_id: Optional[int] = None, current: UserModel = any_role, db: Session = Depends(get_db)
):
    query = select(CategoryModel).order_by(CategoryModel.id)
    if is_user(current):
        # user: глобальні + власні персональні категорії
        check_own_user_id(current, user_id)
        query = query.where(or_(CategoryModel.user_id.is_(None), CategoryModel.user_id == current.id))
    elif user_id is not None:
        # auditor/admin: глобальні + персональні категорії вибраного користувача
        get_or_404(db, UserModel, user_id, "User")
        query = query.where(or_(CategoryModel.user_id.is_(None), CategoryModel.user_id == user_id))
    return db.scalars(query).all()


@app.get("/category/{category_id}", response_model=CategoryOut)
def get_category(category_id: int, current: UserModel = any_role, db: Session = Depends(get_db)):
    return get_visible_category(db, current, category_id)


@app.post("/category", response_model=CategoryOut, status_code=201)
def create_personal_category(
    data: CategoryCreate, current: UserModel = writers, db: Session = Depends(get_db)
):
    check_own_user_id(current, data.user_id)
    owner_id = data.user_id if data.user_id is not None else current.id
    get_or_404(db, UserModel, owner_id, "User")
    category = CategoryModel(name=data.name, user_id=owner_id)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@app.post("/category/global", response_model=CategoryOut, status_code=201)
def create_global_category(
    data: GlobalCategoryCreate, current: UserModel = admin_only, db: Session = Depends(get_db)
):
    category = CategoryModel(name=data.name, user_id=None)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@app.delete("/category/{category_id}", status_code=204)
def delete_category(category_id: int, current: UserModel = writers, db: Session = Depends(get_db)):
    category = get_visible_category(db, current, category_id)
    # глобальні категорії видаляє лише admin, user — тільки свої персональні
    if is_user(current) and category.user_id is None:
        raise ApiError(403, "forbidden", "Access denied: insufficient privileges")
    db.delete(category)
    db.commit()
    return Response(status_code=204)


# Records

@app.post("/record", response_model=RecordOut, status_code=201)
def create_record(data: RecordCreate, current: UserModel = writers, db: Session = Depends(get_db)):
    check_own_user_id(current, data.user_id)
    owner_id = data.user_id if data.user_id is not None else current.id
    get_or_404(db, UserModel, owner_id, "User")
    category = get_visible_category(db, current, data.category_id)
    # варіант 2: персональна категорія має належати власнику витрати
    if category.user_id is not None and category.user_id != owner_id:
        raise ApiError(
            403, "forbidden", f"Category with id {category.id} is a personal category of another user"
        )
    record = RecordModel(
        user_id=owner_id,
        category_id=category.id,
        amount=data.amount,
        **({"created_at": data.created_at} if data.created_at else {}),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@app.get("/record/{record_id}", response_model=RecordOut)
def get_record(record_id: int, current: UserModel = any_role, db: Session = Depends(get_db)):
    return get_visible_record(db, current, record_id)


@app.delete("/record/{record_id}", status_code=204)
def delete_record(record_id: int, current: UserModel = writers, db: Session = Depends(get_db)):
    record = get_visible_record(db, current, record_id)
    db.delete(record)
    db.commit()
    return Response(status_code=204)


@app.get("/record", response_model=List[RecordOut])
def get_records(
    user_id: Optional[int] = None,
    category_id: Optional[int] = None,
    current: UserModel = any_role,
    db: Session = Depends(get_db),
):
    if user_id is None and category_id is None:
        raise ApiError(
            400,
            "invalid_request",
            "At least one query parameter ('user_id' or 'category_id') is required for filtration",
        )
    check_own_user_id(current, user_id)
    if is_user(current):
        # user фільтрує тільки власні записи
        user_id = current.id
    query = select(RecordModel).order_by(RecordModel.id)
    if user_id is not None:
        query = query.where(RecordModel.user_id == user_id)
    if category_id is not None:
        query = query.where(RecordModel.category_id == category_id)
    return db.scalars(query).all()
