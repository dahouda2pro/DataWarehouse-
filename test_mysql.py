
import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

connection = None

try:
    connection = pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        ssl={"check_hostname": True},
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
    )

    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION() AS mysql_version")
        print("Connection successful!")
        print(cursor.fetchone())

except Exception as error:
    print(f"Connection failed: {error}")

finally:
    if connection:
        connection.close()

