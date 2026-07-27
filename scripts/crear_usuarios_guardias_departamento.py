from __future__ import annotations

from actualizar_departamento_guardias import (
    FULL_TIME_DEPARTMENT,
    GUARDIAS_FULL_TIME,
    PART_TIME_DEPARTMENT,
    PART_TIME_NAMES,
    connect,
    department_counts,
    normalize_name,
    normalize_rut,
)


def _fetch_users(cursor) -> list[tuple[int, str, str, str | None]]:
    cursor.execute("SELECT id, [user], name, departament FROM dbo.users")
    return [
        (int(row[0]), str(row[1] or ""), str(row[2] or ""), row[3])
        for row in cursor.fetchall()
    ]


def _fetch_guardias(cursor) -> list[tuple[int, str, str]]:
    cursor.execute("SELECT id, rut, nombre FROM dbo.bbdd_guardias")
    return [
        (int(row[0]), str(row[1] or ""), str(row[2] or ""))
        for row in cursor.fetchall()
    ]


def _insert_user(cursor, rut: str, name: str, department: str) -> None:
    cursor.execute(
        """
        INSERT INTO dbo.users ([user], name, password, role, is_activate, departament)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (rut, name, "", "agent", 1, department),
    )


def main() -> None:
    requested_fulltime = {}
    duplicates = []
    for rut, name in GUARDIAS_FULL_TIME:
        key = normalize_rut(rut)
        if key in requested_fulltime:
            duplicates.append(f"{rut} {name}")
            continue
        requested_fulltime[key] = (rut, name)

    with connect() as conn:
        cursor = conn.cursor()
        before_counts = department_counts(cursor)
        users = _fetch_users(cursor)
        guardias = _fetch_guardias(cursor)

        users_by_rut = {normalize_rut(row[1]): row for row in users if normalize_rut(row[1])}
        guardias_by_name = {normalize_name(row[2]): row for row in guardias if normalize_name(row[2])}

        updated_fulltime = 0
        inserted_fulltime = 0
        for rut_key, (rut, name) in requested_fulltime.items():
            user = users_by_rut.get(rut_key)
            if user:
                if user[3] != FULL_TIME_DEPARTMENT:
                    cursor.execute(
                        "UPDATE dbo.users SET departament = %s WHERE id = %s",
                        (FULL_TIME_DEPARTMENT, user[0]),
                    )
                    updated_fulltime += 1
                continue
            _insert_user(cursor, rut, name, FULL_TIME_DEPARTMENT)
            inserted_fulltime += 1

        updated_parttime = 0
        inserted_parttime = 0
        missing_parttime = []
        users = _fetch_users(cursor)
        users_by_name = {normalize_name(row[2]): row for row in users if normalize_name(row[2])}
        users_by_rut = {normalize_rut(row[1]): row for row in users if normalize_rut(row[1])}

        for name in PART_TIME_NAMES:
            name_key = normalize_name(name)
            guardia = guardias_by_name.get(name_key)
            rut = guardia[1] if guardia else ""
            existing_user = users_by_name.get(name_key) or users_by_rut.get(normalize_rut(rut))
            if existing_user:
                if existing_user[3] != PART_TIME_DEPARTMENT:
                    cursor.execute(
                        "UPDATE dbo.users SET departament = %s WHERE id = %s",
                        (PART_TIME_DEPARTMENT, existing_user[0]),
                    )
                    updated_parttime += 1
                continue
            if not rut:
                missing_parttime.append(name)
                continue
            _insert_user(cursor, rut, name, PART_TIME_DEPARTMENT)
            inserted_parttime += 1

        conn.commit()
        after_counts = department_counts(cursor)

    print("ANTES:", before_counts)
    print("DESPUES:", after_counts)
    print("FULL_TIME_SOLICITADOS_UNICOS:", len(requested_fulltime))
    print("FULL_TIME_INSERTADOS:", inserted_fulltime)
    print("FULL_TIME_ACTUALIZADOS:", updated_fulltime)
    print("PART_TIME_INSERTADOS:", inserted_parttime)
    print("PART_TIME_ACTUALIZADOS:", updated_parttime)
    print("DUPLICADOS_IGNORADOS:", len(duplicates))
    for item in duplicates:
        print("DUPLICADO:", item)
    print("PART_TIME_NO_ENCONTRADOS:", len(missing_parttime))
    for item in missing_parttime:
        print("NO_ENCONTRADO_PART_TIME:", item)


if __name__ == "__main__":
    main()
