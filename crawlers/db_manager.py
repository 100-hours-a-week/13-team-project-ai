import mysql.connector
from mysql.connector import errorcode
import logging
from datetime import datetime

log = logging.getLogger("CrawlerDB")

class DBManager:
    def __init__(self, host="localhost", user="root", password="", database="restaurant_db"):
        self.config = {
            'host': host,
            'user': user,
            'password': password,
            'database': database,
            'raise_on_warnings': False
        }
        self.conn = None
        self._setup_db()

    def _setup_db(self):
        try:
            # Connect without database first to create it if not exists
            temp_config = self.config.copy()
            db_name = temp_config.pop('database')
            conn = mysql.connector.connect(**temp_config)
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name} DEFAULT CHARACTER SET 'utf8mb4'")
            cursor.close()
            conn.close()

            # Now connect to the specific database
            self.conn = mysql.connector.connect(**self.config)
            self._create_tables()
            log.info(f"Connected to MySQL database: {db_name}")
        except mysql.connector.Error as err:
            log.error(f"Failed to connect to MySQL: {err}")
            raise

    def _create_tables(self):
        cursor = self.conn.cursor()
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS restaurants (
            id BIGINT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            road_address VARCHAR(255) NOT NULL,
            jibun_address VARCHAR(255) NULL,
            zipcd VARCHAR(20) NULL,
            lat DECIMAL(10, 7) NOT NULL,
            lng DECIMAL(10, 7) NOT NULL,
            phone VARCHAR(30) NULL,
            category_original VARCHAR(100) NOT NULL,
            category_mapped VARCHAR(50) NOT NULL,
            review_count_visitor INT NULL,
            review_count_blog INT NULL,
            image_url1 VARCHAR(500) NULL,
            image_url2 VARCHAR(500) NULL,
            image_url3 VARCHAR(500) NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        try:
            cursor.execute(create_table_sql)
            # Migration: Add new columns if they don't exist
            migrations = [
                ("jibun_address", "VARCHAR(255) NULL AFTER road_address"),
                ("zipcd", "VARCHAR(20) NULL AFTER jibun_address"),
            ]
            for col, spec in migrations:
                try: cursor.execute(f"ALTER TABLE restaurants ADD COLUMN {col} {spec}")
                except: pass
            
            # Removal: Drop time columns if they exist
            drop_cols = ["open_time", "close_time", "break_start", "break_end", "last_order_time"]
            for col in drop_cols:
                try: cursor.execute(f"ALTER TABLE restaurants DROP COLUMN {col}")
                except: pass

            for i in range(1, 4):
                col = f"image_url{i}"
                try: cursor.execute(f"ALTER TABLE restaurants ADD COLUMN {col} VARCHAR(500) NULL")
                except: pass
            self.conn.commit()
        except mysql.connector.Error as err:
            log.error(f"Failed creating table: {err}")
        finally:
            cursor.close()

    def upsert_restaurant(self, data):
        cursor = self.conn.cursor()
        now = datetime.now()
        
        sql = """
        INSERT INTO restaurants (
            id, name, road_address, jibun_address, zipcd, lat, lng, phone, 
            category_original, category_mapped, 
            review_count_visitor, review_count_blog,
            image_url1, image_url2, image_url3,
            created_at, updated_at
        ) VALUES (
            %(id)s, %(name)s, %(road_address)s, %(jibun_address)s, %(zipcd)s, %(lat)s, %(lng)s, %(phone)s,
            %(category_original)s, %(category_mapped)s,
            %(review_count_visitor)s, %(review_count_blog)s,
            %(image_url1)s, %(image_url2)s, %(image_url3)s,
            %(created_at)s, %(updated_at)s
        ) AS new
        ON DUPLICATE KEY UPDATE
            name = new.name,
            road_address = new.road_address,
            jibun_address = new.jibun_address,
            zipcd = new.zipcd,
            lat = new.lat,
            lng = new.lng,
            phone = new.phone,
            category_original = new.category_original,
            category_mapped = new.category_mapped,
            review_count_visitor = new.review_count_visitor,
            review_count_blog = new.review_count_blog,
            image_url1 = new.image_url1,
            image_url2 = new.image_url2,
            image_url3 = new.image_url3,
            updated_at = new.updated_at
        """
        
        # Add timestamps if not provided
        if 'created_at' not in data: data['created_at'] = now
        if 'updated_at' not in data: data['updated_at'] = now
        
        try:
            cursor.execute(sql, data)
            self.conn.commit()
            log.debug(f"Upserted restaurant: {data['name']} ({data['id']})")
        except mysql.connector.Error as err:
            log.error(f"Error upserting restaurant {data['id']}: {err}")
            self.conn.rollback()
        finally:
            cursor.close()

    def close(self):
        if self.conn:
            self.conn.close()
            log.info("MySQL connection closed.")
