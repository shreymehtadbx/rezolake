"""Generate a small, deliberately messy 3-source customer fixture with ground truth.

Writes SQL (to stdout) that creates <catalog>.rezolake2_fixtures.{crm_contacts, erp_customers,
web_signups, truth}. Sources have CDF enabled so incremental ingest can be exercised.

Usage: python generate_customers.py <catalog> > customers.sql
"""
import random
import sys

random.seed(7)
CAT = sys.argv[1] if len(sys.argv) > 1 else "main"
S = f"`{CAT}`.rezolake2_fixtures"

FIRST = ["John", "Jane", "Robert", "Maria", "David", "Linda", "Michael", "Susan", "William", "Karen",
         "James", "Patricia", "Richard", "Jennifer", "Thomas", "Elizabeth", "Charles", "Barbara", "Daniel",
         "Nancy", "Matthew", "Lisa", "Anthony", "Sandra", "Mark", "Ashley", "Steven", "Emily", "Paul", "Olivia",
         "Andrew", "Sophia", "Joshua", "Grace", "Kevin", "Chloe"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez",
        "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore",
        "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez",
        "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Scott"]
CITY = ["Dallas", "Austin", "Chicago", "Seattle", "Denver", "Boston", "Atlanta", "Phoenix"]
NICK = {"John": "Jon", "Robert": "Bob", "William": "Bill", "Michael": "Mike", "Richard": "Rick",
        "Elizabeth": "Liz", "James": "Jim", "Daniel": "Dan", "Thomas": "Tom", "Jennifer": "Jen",
        "Matthew": "Matt", "Anthony": "Tony", "Steven": "Steve", "Andrew": "Andy", "Joshua": "Josh",
        "Patricia": "Pat", "Susan": "Sue", "Charles": "Chuck", "Barbara": "Barb", "Kevin": "Kev"}


def phone(p):
    return f"{p[:3]}-{p[3:6]}-{p[6:]}"


people = []
for i in range(36):
    f, l = FIRST[i], LAST[i]
    p = f"{random.randint(200, 989)}{random.randint(200, 999)}{random.randint(1000, 9999)}"
    people.append(dict(pid=f"P{i+1:03d}", first=f, last=l, email=f"{f.lower()}.{l.lower()}@example.com",
                       phone=p, city=random.choice(CITY)))
# Confusers: same name, different person.
people.append(dict(pid="P101", first="John", last="Smith", email="jsmith77@mail.com", phone="4155550199", city="Boston"))
people.append(dict(pid="P102", first="Maria", last="Garcia", email="m.garcia@corp.net", phone="3125550142", city="Phoenix"))

crm, erp, web, truth = [], [], [], []
cid = eid = wid = 0
for idx, p in enumerate(people):
    # CRM: most people, clean-ish
    if idx % 7 != 3:
        cid += 1
        crm.append((f"C{cid:04d}", p["first"], p["last"], p["email"], phone(p["phone"]), p["city"],
                    random.choice(["gold", "silver", "bronze"])))
        truth.append(("crm", f"C{cid:04d}", p["pid"]))
        if idx % 9 == 0:  # in-source duplicate with typo'd email case
            cid += 1
            crm.append((f"C{cid:04d}", p["first"], p["last"], p["email"].upper(), phone(p["phone"]), p["city"], "bronze"))
            truth.append(("crm", f"C{cid:04d}", p["pid"]))
    # ERP: full-name style + different phone format; some missing email
    if idx % 3 != 1:
        eid += 1
        given = NICK.get(p["first"], p["first"]) if idx % 4 == 0 else p["first"]
        email = None if idx % 5 == 0 else p["email"]
        erp.append((f"E-{eid:05d}", given.upper(), p["last"].upper(), email,
                    f"+1 ({p['phone'][:3]}) {p['phone'][3:6]} {p['phone'][6:]}", p["city"].upper()))
        truth.append(("erp", f"E-{eid:05d}", p["pid"]))
    # Web: lowercase, nickname, sometimes moved city
    if idx % 2 == 0:
        wid += 1
        fn = NICK.get(p["first"], p["first"]).lower() if idx % 3 == 0 else p["first"].lower()
        city = random.choice(CITY) if idx % 8 == 0 else p["city"]
        web.append((wid, fn, p["last"].lower(), p["email"], p["phone"], city))
        truth.append(("web", str(wid), p["pid"]))


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def rows(rs):
    return ",\n  ".join("(" + ", ".join(lit(v) for v in r) + ")" for r in rs)


CDF = "TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')"
print(f"CREATE SCHEMA IF NOT EXISTS {S};")
print(f"CREATE OR REPLACE TABLE {S}.crm_contacts (contact_id STRING, first_name STRING, last_name STRING, "
      f"email STRING, phone STRING, city STRING, segment STRING) {CDF};")
print(f"INSERT INTO {S}.crm_contacts VALUES\n  {rows(crm)};")
print(f"CREATE OR REPLACE TABLE {S}.erp_customers (cust_no STRING, given_name STRING, family_name STRING, "
      f"email_address STRING, tel STRING, city STRING) {CDF};")
print(f"INSERT INTO {S}.erp_customers VALUES\n  {rows(erp)};")
print(f"CREATE OR REPLACE TABLE {S}.web_signups (user_id INT, fname STRING, lname STRING, email STRING, "
      f"mobile STRING, city STRING) {CDF};")
print(f"INSERT INTO {S}.web_signups VALUES\n  {rows(web)};")
print(f"CREATE OR REPLACE TABLE {S}.truth (source_id STRING, source_key STRING, person_id STRING);")
print(f"INSERT INTO {S}.truth VALUES\n  {rows(truth)};")
print(f"-- counts: crm={len(crm)} erp={len(erp)} web={len(web)} people={len(people)}", file=sys.stderr)
