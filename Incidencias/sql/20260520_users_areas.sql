-- Usuarios por area/departamento para Incidencias.
-- La aplicacion tambien asegura estas tablas al iniciar, pero este SQL deja
-- documentado el cambio de modelo para PostgreSQL.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS department VARCHAR(80);

ALTER TABLE public.login_sessions
    ADD COLUMN IF NOT EXISTS user_id INTEGER,
    ADD COLUMN IF NOT EXISTS user_area_id INTEGER,
    ADD COLUMN IF NOT EXISTS area_code VARCHAR(50),
    ADD COLUMN IF NOT EXISTS department VARCHAR(80);

CREATE TABLE IF NOT EXISTS public.areas (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL UNIQUE,
    department VARCHAR(80) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.user_areas (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    area_id INTEGER NOT NULL REFERENCES public.areas(id) ON DELETE CASCADE,
    department VARCHAR(80) NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_areas_user_area UNIQUE (user_id, area_id)
);

CREATE INDEX IF NOT EXISTS ix_users_department ON public.users(department);
CREATE INDEX IF NOT EXISTS ix_areas_code ON public.areas(code);
CREATE INDEX IF NOT EXISTS ix_areas_department ON public.areas(department);
CREATE INDEX IF NOT EXISTS ix_user_areas_user_id ON public.user_areas(user_id);
CREATE INDEX IF NOT EXISTS ix_user_areas_area_id ON public.user_areas(area_id);
CREATE INDEX IF NOT EXISTS ix_user_areas_department ON public.user_areas(department);
CREATE INDEX IF NOT EXISTS ix_login_sessions_user_id ON public.login_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_login_sessions_user_area_id ON public.login_sessions(user_area_id);

CREATE OR REPLACE VIEW public.users_con_areas AS
SELECT
    ua.id AS user_area_id,
    u.id AS user_id,
    u.name AS usuario,
    u.username,
    u.role,
    u.is_active AS user_is_active,
    a.id AS area_id,
    a.code AS area_code,
    a.name AS area,
    ua.department,
    ua.is_primary
FROM public.user_areas ua
JOIN public.users u ON u.id = ua.user_id
JOIN public.areas a ON a.id = ua.area_id;

INSERT INTO public.areas (code, name, department, is_active)
VALUES
    ('soporte', 'Soporte', 'Soporte', TRUE),
    ('tecnicos', 'Tecnicos', 'Servicio Tecnico', TRUE),
    ('servicio_tecnico', 'Servicio Tecnico', 'Servicio Tecnico', TRUE),
    ('incidencias', 'Incidencias', 'Incidencias', TRUE),
    ('coordinacion', 'Coordinacion', 'Incidencias', TRUE),
    ('protocolos', 'Control de Protocolos', 'Incidencias', TRUE),
    ('venta', 'Venta', 'Venta', TRUE),
    ('finanzas', 'Finanzas', 'Finanzas', TRUE),
    ('administracion', 'Administracion', 'Administracion', TRUE),
    ('operaciones', 'Operaciones', 'Operaciones', TRUE)
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name,
    department = EXCLUDED.department,
    is_active = TRUE;

WITH seed_users(name, username, password, role, primary_department) AS (
    VALUES
        ('Ronald Montilla', 'ronald.montilla', 'plain:RM2025', 'admin', 'Soporte'),
        ('Julissa Mella', 'julissa.mella', 'plain:JM2025', 'agent', 'Soporte'),
        ('Antonio Bahamondes', 'antonio.bahamondes', 'plain:AB2025', 'agent', 'Soporte'),
        ('Sthefan Leal', 'sthefan.leal', 'plain:SL2025', 'agent', 'Soporte'),
        ('Felipe Mora', 'felipe.mora', 'plain:FM2025', 'agent', 'Soporte'),
        ('Fernando Lubiano', 'fernando.lubiano', 'plain:Fernando1180', 'admin', 'Soporte'),
        ('Jason Kevin Pérez Ortiz', 'jason.kevin.perez.ortiz', 'plain:123456', 'agent', 'Servicio Tecnico'),
        ('Carlos Zamora Munita', 'carlos.zamora.munita', 'plain:123456', 'agent', 'Servicio Tecnico'),
        ('Fernando Andrés Lubiano Moraga', 'fernando.andres.lubiano.moraga', 'plain:123456', 'agent', 'Servicio Tecnico'),
        ('Mery Delgado', 'mery.delgado', 'plain:123456', 'agent', 'Incidencias'),
        ('Cristian Olivares', 'cristian.olivares', 'plain:123456', 'agent', 'Incidencias'),
        ('Héctor Rosales', 'hector.rosales', 'plain:123456', 'agent', 'Incidencias'),
        ('Angélica Guerra', 'angelica.guerra', 'plain:123456', 'agent', 'Incidencias'),
        ('Nicolas Santibañez', 'nicolas.santibanez', 'plain:123456', 'agent', 'Incidencias'),
        ('Daisy Vergara', 'daisy.vergara', 'plain:123456', 'agent', 'Incidencias'),
        ('Tahira Riquelme', 'tahira.riquelme', 'plain:123456', 'agent', 'Incidencias'),
        ('Marian Macho', 'marian.macho', 'plain:123456', 'agent', 'Incidencias'),
        ('Manuel Mondaca', 'manuel.mondaca', 'plain:123456', 'agent', 'Incidencias'),
        ('Teodoro Storm', 'teodoro.storm', 'plain:123456', 'agent', 'Venta'),
        ('Gianpiero Lubiano', 'gianpiero.lubiano', 'plain:123456', 'agent', 'Venta'),
        ('Lucas Cortes', 'lucas.cortes', 'plain:123456', 'agent', 'Venta'),
        ('Sebastian Storm', 'sebastian.storm', 'plain:123456', 'agent', 'Venta'),
        ('Giancarlo Lubiano', 'giancarlo.lubiano', 'plain:123456', 'agent', 'Finanzas'),
        ('Maryorie Alegría', 'maryorie.alegria', 'plain:123456', 'agent', 'Administracion')
)
INSERT INTO public.users (name, username, hashed_password, role, department, is_active)
SELECT name, username, password, role, primary_department, TRUE
FROM seed_users
ON CONFLICT (username) DO UPDATE
SET name = EXCLUDED.name,
    role = EXCLUDED.role,
    department = COALESCE(public.users.department, EXCLUDED.department),
    is_active = TRUE;

WITH memberships(username, area_code, is_primary) AS (
    VALUES
        ('ronald.montilla', 'soporte', TRUE),
        ('julissa.mella', 'soporte', TRUE),
        ('antonio.bahamondes', 'soporte', TRUE),
        ('sthefan.leal', 'soporte', TRUE),
        ('felipe.mora', 'soporte', TRUE),
        ('fernando.lubiano', 'soporte', TRUE),
        ('jason.kevin.perez.ortiz', 'servicio_tecnico', TRUE),
        ('jason.kevin.perez.ortiz', 'tecnicos', FALSE),
        ('carlos.zamora.munita', 'servicio_tecnico', TRUE),
        ('carlos.zamora.munita', 'operaciones', FALSE),
        ('carlos.zamora.munita', 'tecnicos', FALSE),
        ('fernando.andres.lubiano.moraga', 'servicio_tecnico', TRUE),
        ('fernando.andres.lubiano.moraga', 'tecnicos', FALSE),
        ('mery.delgado', 'incidencias', TRUE),
        ('mery.delgado', 'coordinacion', FALSE),
        ('mery.delgado', 'protocolos', FALSE),
        ('cristian.olivares', 'incidencias', TRUE),
        ('cristian.olivares', 'coordinacion', FALSE),
        ('cristian.olivares', 'protocolos', FALSE),
        ('hector.rosales', 'incidencias', TRUE),
        ('hector.rosales', 'coordinacion', FALSE),
        ('hector.rosales', 'protocolos', FALSE),
        ('angelica.guerra', 'incidencias', TRUE),
        ('angelica.guerra', 'coordinacion', FALSE),
        ('angelica.guerra', 'protocolos', FALSE),
        ('nicolas.santibanez', 'incidencias', TRUE),
        ('nicolas.santibanez', 'coordinacion', FALSE),
        ('nicolas.santibanez', 'protocolos', FALSE),
        ('daisy.vergara', 'incidencias', TRUE),
        ('daisy.vergara', 'coordinacion', FALSE),
        ('daisy.vergara', 'protocolos', FALSE),
        ('tahira.riquelme', 'incidencias', TRUE),
        ('tahira.riquelme', 'coordinacion', FALSE),
        ('tahira.riquelme', 'protocolos', FALSE),
        ('marian.macho', 'incidencias', TRUE),
        ('marian.macho', 'coordinacion', FALSE),
        ('marian.macho', 'protocolos', FALSE),
        ('manuel.mondaca', 'incidencias', TRUE),
        ('manuel.mondaca', 'coordinacion', FALSE),
        ('manuel.mondaca', 'protocolos', FALSE),
        ('teodoro.storm', 'venta', TRUE),
        ('gianpiero.lubiano', 'venta', TRUE),
        ('lucas.cortes', 'venta', TRUE),
        ('sebastian.storm', 'venta', TRUE),
        ('giancarlo.lubiano', 'finanzas', TRUE),
        ('maryorie.alegria', 'administracion', TRUE)
)
INSERT INTO public.user_areas (user_id, area_id, department, is_primary)
SELECT u.id, a.id, a.department, m.is_primary
FROM memberships m
JOIN public.users u ON u.username = m.username
JOIN public.areas a ON a.code = m.area_code
ON CONFLICT (user_id, area_id) DO UPDATE
SET department = EXCLUDED.department,
    is_primary = public.user_areas.is_primary OR EXCLUDED.is_primary;
