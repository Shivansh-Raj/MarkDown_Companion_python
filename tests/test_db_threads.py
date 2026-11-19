import threading

from app.services.persistence import Database


def test_settings_can_be_written_from_a_worker_thread(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    errors = []

    def work(n):
        try:
            for i in range(50):
                db.set_setting(f"k{n}", i)
                db.get_setting(f"k{n}")
        except Exception as e:          # the bug: "SQLite objects created in a thread can only be used in that same thread"
            errors.append(e)
    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and db.get_setting("k3") == 49
