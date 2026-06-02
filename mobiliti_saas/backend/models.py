from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    nombre = Column(String)
    empresa = Column(String)
    es_admin = Column(Boolean, default=False)
    activo = Column(Boolean, default=True)
    creado = Column(DateTime, default=datetime.utcnow)

    suscripcion = relationship("Suscripcion", back_populates="usuario", uselist=False)
    sesiones = relationship("SesionCliente", back_populates="usuario")


class Suscripcion(Base):
    __tablename__ = "suscripciones"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), unique=True)
    estado = Column(String, default="activa")  # activa, vencida, suspendida, cancelada
    plan = Column(String, default="mensual")   # mensual, anual
    fecha_inicio = Column(DateTime, default=datetime.utcnow)
    fecha_fin = Column(DateTime)
    creado = Column(DateTime, default=datetime.utcnow)
    actualizado = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    usuario = relationship("Usuario", back_populates="suscripcion")


class SesionCliente(Base):
    __tablename__ = "sesiones_cliente"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"))
    hardware_id = Column(String)
    token = Column(String)
    activa = Column(Boolean, default=True)
    ultimo_acceso = Column(DateTime, default=datetime.utcnow)
    creado = Column(DateTime, default=datetime.utcnow)

    usuario = relationship("Usuario", back_populates="sesiones")
