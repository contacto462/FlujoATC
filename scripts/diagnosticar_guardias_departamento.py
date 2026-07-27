from __future__ import annotations

from actualizar_departamento_guardias import (
    GUARDIAS_FULL_TIME,
    PART_TIME_NAMES,
    connect,
    normalize_name,
    normalize_rut,
)


def main() -> None:
    requested = {}
    for rut, name in GUARDIAS_FULL_TIME:
        requested.setdefault(normalize_rut(rut), (rut, name))

    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, [user], name, departament FROM dbo.users")
        users = cursor.fetchall()
        users_by_rut = {normalize_rut(row[1]): row for row in users if normalize_rut(row[1])}
        users_by_name = {normalize_name(row[2]): row for row in users if normalize_name(row[2])}

        cursor.execute("SELECT id, rut, nombre FROM dbo.bbdd_guardias")
        guardias = cursor.fetchall()
        guardias_by_rut = {normalize_rut(row[1]): row for row in guardias if normalize_rut(row[1])}

        matched_users = [users_by_rut[key] for key in requested if key in users_by_rut]
        matched_guardias = [guardias_by_rut[key] for key in requested if key in guardias_by_rut]
        missing_guardias = [
            f"{rut} {name}"
            for key, (rut, name) in requested.items()
            if key not in guardias_by_rut
        ]

        print("SOLICITADOS_UNICOS:", len(requested))
        print("EN_DBO_USERS:", len(matched_users))
        for row in matched_users:
            print("USER_MATCH:", row[1], row[2], row[3])
        print("EN_BBDD_GUARDIAS:", len(matched_guardias))
        print("NO_EN_BBDD_GUARDIAS:", len(missing_guardias))
        for row in missing_guardias[:40]:
            print("NO_EN_BBDD_GUARDIAS_ITEM:", row)
        for name in PART_TIME_NAMES:
            key = normalize_name(name)
            row = users_by_name.get(key)
            print("PART_TIME_USER_MATCH:", name, row[1:] if row else None)
            row_guardia = next(
                (item for item in guardias if normalize_name(item[2]) == key),
                None,
            )
            print("PART_TIME_BBDD_GUARDIA_MATCH:", name, row_guardia[1:] if row_guardia else None)
        cursor.execute(
            "SELECT COUNT(*) FROM dbo.users WHERE departament = 'Bitacora' OR departament LIKE '%Bitacora%'"
        )
        print("USERS_CON_BITACORA:", cursor.fetchone()[0])


if __name__ == "__main__":
    main()
