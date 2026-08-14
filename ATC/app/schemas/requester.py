from pydantic import BaseModel


class RequesterCreate(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    type: str = "external"


class RequesterOut(BaseModel):
    id: int
    name: str | None
    email: str | None
    phone: str | None
    type: str

    class Config:
        from_attributes = True