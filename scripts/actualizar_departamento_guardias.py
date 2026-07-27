from __future__ import annotations

import getpass
import os
import unicodedata
from dataclasses import dataclass

import pymssql


SERVER = os.getenv("ATC_SQL_SERVER", "10.20.30.8")
PORT = int(os.getenv("ATC_SQL_PORT", "14330"))
DATABASE = os.getenv("ATC_SQL_DATABASE", "PROYECTO_ATC")
USERNAME = os.getenv("ATC_SQL_USER", "atc_vscode")

FULL_TIME_DEPARTMENT = "GuardiasFullTime"
PART_TIME_DEPARTMENT = "GuardiaPartTime"
PART_TIME_NAMES = ["Jeremy Abraham Luck Cisternas"]


GUARDIAS_FULL_TIME = [
    ("15818727-2", "Pedro Abino Abarca Navea"),
    ("11851964-7", "Simon Andres Aguilera Vargas"),
    ("7880196-4", "Patricio Fernando Araya Gatica"),
    ("15732488-8", "Jose David Donoso Sanchez"),
    ("20270613-4", "Francisco Javier Ibarra Moya"),
    ("18481567-2", "Yesenia Ailyn Canelo Rodriguez"),
    ("18032390-2", "Cecilia Elizabeth Carreño Gonzalez"),
    ("17976829-1", "Hector Jaime Cisternas Almendrares"),
    ("9066030-6", "Victor Manuel Cisternas Lopez"),
    ("10515825-4", "Claudio Alonso Diaz Medel"),
    ("26028721-4", "Erika Pilar Dupont Giraldo"),
    ("6551601-2", "Carlos Sergio Gutierrez Rojas"),
    ("18843299-9", "Felipe Raul Hernandez Arismendi"),
    ("20504776-k", "Amanda Florencia Ipinza Espinoza"),
    ("18695591-9", "Priscilla Madeleine Jorquera Cura"),
    ("17976719-8", "Luis Geovanni Martinez Barraza"),
    ("8330003-5", "Manuel Gregorio Meza Valdivia"),
    ("13994006-7", "Alejandro Mauricio Moran Fernandez"),
    ("13414837-3", "Marta Ines Ortiz Lucero"),
    ("6599258-2", "Julio Ramiro Ostornol Meneses"),
    ("13710149-1", "Pablo Andres Bravo Ramos"),
    ("8569839-7", "Leonel Alfonso Pereira Medeli"),
    ("8131734-8", "Janet Patricia Pozas Rodriguez"),
    ("20455091-3", "Yanira Alysson Reyes Leiva"),
    ("17222140-8", "Leslie Dayana Rivas Mardones"),
    ("18306503-3", "Ignacio Alejandro Villanueva Peña"),
    ("19383416-7", "Carlos Esteban Sanchez Massina"),
    ("20296495-8", "Alejandro Antonio Miranda Rojas"),
    ("18922054-5", "Johan Andres Cyperdiuk Tapia"),
    ("21779741-1", "Charles Felipe Atton Becerra"),
    ("12025471-5", "Jacqueline Constancia Cordero Meriño"),
    ("19585292-8", "Victor David Funes Medina"),
    ("15459965-7", "Carlos Ulises Altamirano Muñoz"),
    ("9347620-4", "Carlos Manuel Arancibia Ojeda"),
    ("18854610-2", "Igdalias Alejandro Arenas Oliva"),
    ("19615416-7", "Ian Alexis Bernal Martinez"),
    ("10256607-6", "Cristian Ruben Burgos Ramirez"),
    ("9918658-5", "Eugenio Alejandro Contreras Salas"),
    ("12025471-5", "Jacqueline Constancia Cordero Meriño"),
    ("14424541-5", "Eduvina Del Carmen Gonzalez Ojeda"),
    ("18554370-6", "Rocio Olivia Gonzalez Ramos"),
    ("16989622-4", "Nicolas Alberto Jorquera Alfaro"),
    ("17085912-k", "Nicolas Andres Maluenda Carlini"),
    ("18571076-9", "Marcelo Alfredo Mancilla Tello"),
    ("16441769-7", "Jeniffer Dominique Melgarejo Garrido"),
    ("19336915-4", "Bruno Oyaneder Verdejo"),
    ("13237724-3", "Ismael Andres Palomera Zenteno"),
    ("12399522-8", "Maribel Del Rosario Ossandon Manterola"),
    ("9951617-8", "Adolfo Segundo Sepulveda Zamora"),
    ("13942687-8", "David Antonio Vasquez Arenas"),
    ("10261572-7", "Jessica Del Transito Oyarse Aguilar"),
    ("16065109-1", "Caterine Miño Gonzalez"),
    ("13715519-2", "Felipe Andres Orellana Biava"),
    ("8958882-0", "Milton Alejandro Bravo Diaz"),
    ("9410773-3", "Ana Del Carmen Solari Moreno"),
    ("16144156-2", "Claudio Esteban Contreras Ramos"),
    ("20299695-7", "Paul Alejandro Carvajal Diaz"),
    ("9572754-9", "Juan Carlos Chandia Ramirez"),
    ("17943329-k", "Robinson Christian Muñoz Corrales"),
    ("13982872-0", "Peter Robert Villarroel Mattisine"),
    ("9643054-k", "Jessica Perla Valdes Carrasco"),
    ("20904742-k", "Juan Sebastian Huircapan Mesias"),
    ("20916977-0", "Jonatan Alexander Sanchez Messina"),
    ("19972775-3", "Rodrigo Antonio Alarcón Ramírez"),
    ("15386619-8", "Jorge Andres Ramirez Benavides"),
    ("18554438-9", "Jean Paul Arancibia Aballay"),
    ("19034661-7", "Enrique Andres Yañez Marin"),
    ("21285714-9", "Alexander Jesus Mesias Lab"),
    ("15973077-8", "Miguel Angel Mardones Carrasco"),
    ("17740142-0", "Diego Andres Salas Montes"),
    ("18913444-4", "Solange Corina Vilches Llanten"),
    ("20502178-7", "Sebastian Fernando Vargas Carreras"),
    ("9553506-2", "Harry Patricio Jerez Asgmad"),
    ("16637732-3", "Ivan Esteban Bravo Leon"),
    ("20045623-8", "Daniel Alejandro Ortiz Hidalgo"),
    ("9725725-6", "Hector Raul Sepulveda Ordoñez"),
    ("19181248-4", "Matias Antonio Arancibia Morales"),
    ("16698870-5", "Raul Alejandro Zuñiga Ibañez"),
    ("12483799-5", "Magaly Andrea Zuñiga Morales"),
    ("12683323-7", "Atilio Romualdo Villacura Mesa"),
    ("9707982-k", "Dante Mario Alvarez Gonzalez"),
    ("14137621-7", "Ana Lorena Hidalgo Panes"),
    ("14040068-8", "Nancy Isabel Barria Barria"),
    ("18917558-2", "Moisés Osses Navarro"),
    ("16970634-4", "Rodrigo Antonio Palacios Fernandez"),
    ("13060044-1", "Rodrigo Alejandro Bustos Bustos"),
    ("13430452-9", "Cesar Riveros Fernandez"),
    ("8506945-4", "Larisa Ivonova Cisternas Aracena"),
    ("13124795-8", "Julio Humberto Reyes Castro"),
    ("16777869-0", "Carolina Jazmina Vivanco Ossandon"),
    ("13376450-k", "Elvira Carolina Muñoz Flores"),
    ("14536012-9", "Marcial Antonio Nahuelpi Villar"),
    ("19068078-9", "Marcelo Hernan Gomez Meneses"),
    ("19941612-k", "Cristian Andres Ortega Huerta"),
    ("10624674-2", "Jacqueline Sanhueza Sanhueza"),
    ("9529988-1", "Myriam Del Carmen Pizarro Fuentes"),
    ("17874709-6", "Gustavo Andrés Zárate Olea"),
    ("14131733-4", "Nelson Hernan Mora Parada"),
    ("17807898-4", "Sebastian Andres Lira Pique"),
    ("11825040-0", "Willy Ernesto Luck Flores"),
    ("10264272-4", "Marina Alvarez Haverbeck"),
    ("11796536-8", "Maria Teresa Henriquez Jaque"),
    ("17816538-0", "Franco Gabriel Donoso Gonzalez"),
    ("14233034-2", "Rosa Ester Gonzalez Soto"),
    ("17739749-0", "Alejandro Andres Santander Provoste"),
    ("17815630-6", "Ariel Rodrigo Silva Caceres"),
]


@dataclass
class UserRow:
    id: int
    username: str
    name: str
    department: str | None


def normalize_rut(value: object) -> str:
    text = str(value or "").strip().upper()
    return "".join(ch for ch in text if ch not in ". -")


def normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold().strip())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.split())


def connect():
    password = os.getenv("ATC_SQL_PASSWORD") or getpass.getpass("Password SQL Server: ")
    return pymssql.connect(
        server=SERVER,
        port=PORT,
        user=USERNAME,
        password=password,
        database=DATABASE,
        login_timeout=10,
        timeout=30,
    )


def fetch_users(cursor) -> list[UserRow]:
    cursor.execute("SELECT id, [user], name, departament FROM dbo.users")
    return [
        UserRow(
            id=int(row[0]),
            username=str(row[1] or ""),
            name=str(row[2] or ""),
            department=row[3],
        )
        for row in cursor.fetchall()
    ]


def department_counts(cursor) -> dict[str, int]:
    cursor.execute(
        """
        SELECT COALESCE(departament, ''), COUNT(*)
        FROM dbo.users
        WHERE departament IN (%s, %s)
        GROUP BY departament
        """,
        (FULL_TIME_DEPARTMENT, PART_TIME_DEPARTMENT),
    )
    return {str(row[0]): int(row[1]) for row in cursor.fetchall()}


def main() -> None:
    requested_by_rut: dict[str, tuple[str, str]] = {}
    duplicates: list[str] = []
    for rut, name in GUARDIAS_FULL_TIME:
        key = normalize_rut(rut)
        if key in requested_by_rut:
            duplicates.append(f"{rut} {name}")
            continue
        requested_by_rut[key] = (rut, name)

    with connect() as conn:
        cursor = conn.cursor()
        before_counts = department_counts(cursor)
        users = fetch_users(cursor)
        by_rut = {normalize_rut(user.username): user for user in users if normalize_rut(user.username)}
        by_name = {normalize_name(user.name): user for user in users if normalize_name(user.name)}

        missing = []
        fulltime_updates = []
        for rut_key, (rut, expected_name) in requested_by_rut.items():
            user = by_rut.get(rut_key)
            if not user:
                missing.append(f"{rut} {expected_name}")
                continue
            if user.department != FULL_TIME_DEPARTMENT:
                cursor.execute(
                    "UPDATE dbo.users SET departament = %s WHERE id = %s",
                    (FULL_TIME_DEPARTMENT, user.id),
                )
                fulltime_updates.append((user.username, user.name, user.department))

        parttime_updates = []
        missing_parttime = []
        for name in PART_TIME_NAMES:
            name_key = normalize_name(name)
            user = by_name.get(name_key)
            if not user:
                tokens = set(name_key.split())
                candidates = [
                    item for item in users
                    if tokens.issubset(set(normalize_name(item.name).split()))
                ]
                user = candidates[0] if len(candidates) == 1 else None
            if not user:
                missing_parttime.append(name)
                continue
            if user.department != PART_TIME_DEPARTMENT:
                cursor.execute(
                    "UPDATE dbo.users SET departament = %s WHERE id = %s",
                    (PART_TIME_DEPARTMENT, user.id),
                )
                parttime_updates.append((user.username, user.name, user.department))

        conn.commit()

        after_counts = department_counts(cursor)

    print("ANTES:", before_counts)
    print("DESPUES:", after_counts)
    print("FULL_TIME_SOLICITADOS_UNICOS:", len(requested_by_rut))
    print("FULL_TIME_ACTUALIZADOS:", len(fulltime_updates))
    print("PART_TIME_ACTUALIZADOS:", len(parttime_updates))
    print("DUPLICADOS_IGNORADOS:", len(duplicates))
    if duplicates:
        for row in duplicates:
            print("DUPLICADO:", row)
    print("FULL_TIME_NO_ENCONTRADOS:", len(missing))
    if missing:
        for row in missing:
            print("NO_ENCONTRADO:", row)
    print("PART_TIME_NO_ENCONTRADOS:", len(missing_parttime))
    if missing_parttime:
        for row in missing_parttime:
            print("NO_ENCONTRADO_PART_TIME:", row)


if __name__ == "__main__":
    main()
