---
tipo: moc
tags:
  - moc
actualizado: 2026-06-14
---

# 🏠 ATC · Centro del Vault

> [!abstract] Qué es esto
> Base de conocimiento del proyecto **ATC** (monorepo FastAPI de Alguien Te Cuida). Empieza por [[Visión General]].

## 🧭 Secciones
- 🏗️ **Arquitectura** → [[Visión General]] · [[App Unificada]] · [[Login Único y SSO]]
- 🧩 **Módulos** → [[Helpdesk]] · [[Incidencias]] · [[Venta]]
- 🗄️ **Base de datos** → [[MER ATC]] · [[Unificación BBDD]]
- ⚙️ **Operaciones** → [[Levantar Servidor]] · [[Email IMAP-SMTP]] · [[Celery y Workers]]
- 🧠 **Decisiones** → [[0001 Unificación de BBDD]]
- 📓 **Bitácora** → carpeta `50-Bitácora` (nota diaria)

## 🧩 Módulos
![[Módulos.base]]

## ✅ Tareas
![[Tareas.base]]

## 🗄️ Tablas de la BBDD
![[Tablas BBDD.base]]

## 🧠 Decisiones (ADR)
![[Decisiones.base]]

---
> [!tip] Cómo crecer el vault
> - Notas nuevas usan las plantillas de `_templates/` (comando *Templates: Insert template*).
> - Respeta el frontmatter (`tipo`, `modulo`, `estado`, `tags`) para que las bases las recojan.
> - Tags útiles: `#modulo/helpdesk`, `#modulo/incidencias`, `#modulo/venta`, `#db/tabla`, `#op/runbook`, `#decision`.
