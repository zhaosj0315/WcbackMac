# -*- coding: utf-8 -*-
import sqlite3
import os
from dbutils.pooled_db import PooledDB

class DatabaseBase:
    _db_pool = {}
    existed_tables = []
    
    def __init__(self, db_config):
        self.config = db_config
        self.pool = self.connect(self.config)
        self._get_existed_tables()
    
    @classmethod
    def connect(cls, db_config):
        db_path = db_config.get("path", "")
        db_key = db_config.get("key", db_path)
        
        if db_key in cls._db_pool:
            return cls._db_pool[db_key]
        
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"数据库不存在: {db_path}")
        
        pool = PooledDB(
            creator=sqlite3,
            maxconnections=0,
            mincached=4,
            maxusage=1,
            blocking=True,
            database=db_path
        )
        cls._db_pool[db_key] = pool
        return pool
    
    def _get_existed_tables(self):
        sql = "SELECT tbl_name FROM sqlite_master WHERE type='table'"
        result = self.execute(sql)
        if result:
            self.existed_tables = [row[0].lower() for row in result]
    
    def tables_exist(self, required_tables):
        if isinstance(required_tables, str):
            required_tables = [required_tables]
        return all(t.lower() in self.existed_tables for t in required_tables)
    
    def execute(self, sql, params=None):
        conn = self.pool.connection()
        try:
            cursor = conn.cursor()
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            return cursor.fetchall()
        except Exception as e:
            print(f"SQL错误: {e}")
            return None
        finally:
            conn.close()
    
    def add_index(self, table, columns):
        """添加索引加速查询"""
        for col in columns:
            idx_name = f"idx_{table}_{col}"
            sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({col})"
            self.execute(sql)
