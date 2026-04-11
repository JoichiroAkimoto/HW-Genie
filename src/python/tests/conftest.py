import pytest
from hw_genie.core.database import init_db, Base, engine

@pytest.fixture(autouse=True)
def setup_db():
    # メモリ上（もしくは書き込み可能なテスト用DB）で実行
    # テスト環境では :memory: を使うのがSQLAlchemyの定石です
    init_db()
    yield
    # テスト後にテーブルをドロップ（またはファイル削除）
    Base.metadata.drop_all(engine)
