import sqlite3
import os
import inspect
from threading import Lock, local, current_thread
from datetime import datetime
from nxdrive.logging_config import get_logger
from PyQt4.QtCore import pyqtSignal, QObject
log = get_logger(__name__)

SCHEMA_VERSION = "schema_version"

# Summary status from last known pair of states

PAIR_STATES = {
    # regular cases
    ('unknown', 'unknown'): 'unknown',
    ('synchronized', 'synchronized'): 'synchronized',
    ('created', 'unknown'): 'locally_created',
    ('unknown', 'created'): 'remotely_created',
    ('modified', 'synchronized'): 'locally_modified',
    ('moved', 'synchronized'): 'locally_moved',
    ('moved', 'deleted'): 'locally_moved_created',
    ('moved', 'modified'): 'locally_moved_remotely_modified',
    ('synchronized', 'modified'): 'remotely_modified',
    ('modified', 'unknown'): 'locally_modified',
    ('unknown', 'modified'): 'remotely_modified',
    ('deleted', 'synchronized'): 'locally_deleted',
    ('synchronized', 'deleted'): 'remotely_deleted',
    ('deleted', 'deleted'): 'deleted',
    ('synchronized', 'unknown'): 'synchronized',

    # conflicts with automatic resolution
    ('created', 'deleted'): 'locally_created',
    ('deleted', 'created'): 'remotely_created',
    ('modified', 'deleted'): 'remotely_deleted',
    ('deleted', 'modified'): 'remotely_created',

    # conflict cases that need manual resolution
    ('modified', 'modified'): 'conflicted',
    ('created', 'created'): 'conflicted',
    ('created', 'modified'): 'conflicted',

    # inconsistent cases
    ('unknown', 'deleted'): 'unknown_deleted',
    ('deleted', 'unknown'): 'deleted_unknown',
}


class CustomRow(sqlite3.Row):

    def __init__(self, arg1, arg2):
        super(CustomRow, self).__init__(arg1, arg2)
        self._custom = dict()

    def __getattr__(self, name):
        if name in self._custom:
            return self._custom[name]
        return self[name]

    def __setattr__(self, name, value):
        if name.startswith('_'):
            super(CustomRow, self).__setattr__(name,value)
        else:
            self._custom[name] = value

    def __delattr__(self, name):
        if name.startswith('_'):
            super(CustomRow, self).__detattr__(name)
        else:
            del self._custom[name]


class StateRow(CustomRow):
    _custom = None

    def is_readonly(self):
        if self.folderish:
            return self.remote_can_create_child == 0
        else:
            return (self.remote_can_delete & self.remote_can_rename
                        & self.remote_can_update) == 0

    def update_state(self, local_state=None, remote_state=None):
        if local_state is not None:
            self.local_state = local_state
        if remote_state is not None:
            self.remote_state = remote_state

    def __repr__(self):
        return self.__unicode__().encode('ascii', 'ignore')

    def __unicode__(self):
        return u"%s[%d](Local: %r, Remote: %s, Local state: %s, Remote state: %s, State: %s)" % (
            self.__class__.__name__, self.id, self.local_path, self.remote_ref, self.local_state, self.remote_state,
            self.pair_state)


class LogLock(object):
    def __init__(self):
        self._lock = Lock()

    def acquire(self):
        log.trace("lock acquire: %s", inspect.stack()[1][3])
        self._lock.acquire()
        log.trace("lock acquired")

    def release(self):
        log.trace("lock release: %s", inspect.stack()[1][3])
        self._lock.release()


class FakeLock(object):
    def acquire(self):
        pass

    def release(self):
        pass


class ConfigurationDAO(QObject):
    '''
    classdocs
    '''

    def __init__(self, db):
        '''
        Constructor
        '''
        super(ConfigurationDAO, self).__init__()
        log.debug("Create DAO on %s", db)
        self._db = db
        migrate = os.path.exists(self._db)
        # For testing purpose only should always be True
        self.share_connection = True
        self.auto_commit = True
        self.schema_version = 1
        self.in_tx = None
        self._tx_lock = Lock()
        # If we dont share connection no need to lock
        if self.share_connection:
            self._lock = Lock()
        else:
            self._lock = FakeLock()
        # Use to clean
        self._connections = []
        self._create_main_conn()
        self._conn.row_factory = CustomRow
        c = self._conn.cursor()
        self._init_db(c)
        if migrate:
            res = c.execute("SELECT value FROM Configuration WHERE name='"+SCHEMA_VERSION+"'").fetchone()
            if res is None:
                schema = 0
            else:
                schema = int(res[0])
            if schema != self.schema_version:
                self._migrate_db(c, schema)
        self._conn.commit()
        self._conns = local()
        # FOR PYTHON 3.3...
        #if log.getEffectiveLevel() < 6:
        #    self._conn.set_trace_callback(self._log_trace)

    def get_db(self):
        return self._db

    def _migrate_table(self, cursor, name):
        # Add the last_transfer
        tmpname = name + 'Migration'
        cursor.execute("ALTER TABLE " + name + " RENAME TO " + tmpname)
        # Because Windows dont release the table, force the creation
        self._create_state_table(cursor, force=True)
        target_cols = self._get_columns(cursor, name)
        source_cols = self._get_columns(cursor, tmpname)
        cols = ', '.join(set(target_cols).intersection(source_cols))
        cursor.execute("INSERT INTO " + name + "(" + cols + ") SELECT " + cols + " FROM " + tmpname)
        cursor.execute("DROP TABLE " + tmpname)

    def _get_columns(self, cursor, table):
        cols = []
        res = cursor.execute("PRAGMA table_info('" + table + "')").fetchall()
        for col in res:
            cols.append(col.name)
        return cols

    def _migrate_db(self, cursor, version):
        if version < 1:
            self.update_config(SCHEMA_VERSION, 1)

    def _init_db(self, cursor):
        # http://www.stevemcarthur.co.uk/blog/post/some-kind-of-disk-io-error-occurred-sqlite
        cursor.execute("PRAGMA journal_mode = MEMORY")
        cursor.execute("CREATE TABLE if not exists Configuration(name VARCHAR NOT NULL, value VARCHAR, PRIMARY KEY (name))")

    def _create_main_conn(self):
        log.debug("Create main connexion on %s (dir exists: %d / file exists: %d)",
                    self._db, os.path.exists(os.path.dirname(self._db)), os.path.exists(self._db))
        self._conn = sqlite3.connect(self._db, check_same_thread=False)
        self._connections.append(self._conn)

    def _log_trace(self, query):
        log.trace(query)

    def dispose(self):
        log.debug("Disposing sqlite database %r", self.get_db())
        for con in self._connections:
            con.close()
        self._connections = []
        self._conn = None

    def dispose_thread(self):
        if not hasattr(self._conns, '_conn'):
            return
        if self._conns._conn in self._connections:
            self._connections.remove(self._conns._conn)
        self._conns._conn = None

    def _get_write_connection(self, factory=CustomRow):
        if self.share_connection or self.in_tx:
            if self._conn is None:
                self._create_main_conn()
            self._conn.row_factory = factory
            return self._conn
        return self._get_read_connection(factory)

    def _get_read_connection(self, factory=CustomRow):
        # If in transaction
        if self.in_tx is not None:
            if current_thread().ident != self.in_tx:
                log.trace("In transaction wait for read connection")
                # Wait for the thread in transaction to finished
                self._tx_lock.acquire()
                self._tx_lock.release()
            else:
                # Return the write connection
                return self._conn
        if not hasattr(self._conns, '_conn') or self._conns._conn is None:
            # Dont check same thread for closing purpose
            self._conns._conn = sqlite3.connect(self._db, check_same_thread=False)
            self._connections.append(self._conns._conn)
        self._conns._conn.row_factory = factory
            # Python3.3 feature
            #if log.getEffectiveLevel() < 6:
            #    self._conns._conn.set_trace_callback(self._log_trace)
        return self._conns._conn

    def begin_transaction(self):
        self.auto_commit = False
        self._tx_lock.acquire()
        self.in_tx = current_thread().ident

    def end_transaction(self):
        self.auto_commit = True
        self._lock.acquire()
        self._get_write_connection().commit()
        self._lock.release()
        self._tx_lock.release()
        self.in_tx = None

    def commit(self):
        if self.auto_commit:
            return
        self._lock.acquire()
        try:
            self._get_write_connection().commit()
        finally:
            self._lock.release()

    def delete_config(self, name):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("DELETE FROM Configuration WHERE name=?", (name,))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def update_config(self, name, value):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            if value is not None:
                c.execute("UPDATE OR IGNORE Configuration SET value=? WHERE name=?", (value,name))
                c.execute("INSERT OR IGNORE INTO Configuration(value,name) VALUES(?,?)", (value,name))
            else:
                c.execute("DELETE FROM Configuration WHERE name=?", (name,))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def get_config(self, name, default=None):
        c = self._get_read_connection().cursor()
        obj = c.execute("SELECT value FROM Configuration WHERE name=?",(name,)).fetchone()
        if obj is None:
            return default
        return obj.value


class ManagerDAO(ConfigurationDAO):
    def __init__(self, db):
        super(ManagerDAO, self).__init__(db)

    def _init_db(self, cursor):
        super(ManagerDAO, self)._init_db(cursor)
        cursor.execute("CREATE TABLE if not exists Engines(uid VARCHAR, engine VARCHAR NOT NULL, name VARCHAR, local_folder VARCHAR NOT NULL UNIQUE, PRIMARY KEY(uid))")

    def get_engines(self):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM Engines").fetchall()

    def add_engine(self, engine, path, key, name):
        result = None
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("INSERT INTO Engines(local_folder, engine, uid, name) VALUES(?,?,?,?)", (path, engine, key, name))
            if self.auto_commit:
                con.commit()
            result = c.execute("SELECT * FROM Engines WHERE uid=?", (key,)).fetchone()
        finally:
            self._lock.release()
        return result

    def delete_engine(self, uid):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("DELETE FROM Engines WHERE uid=?", (uid,))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()


class EngineDAO(ConfigurationDAO):
    '''
    classdocs
    '''
    newConflict = pyqtSignal(object)
    def __init__(self, db):
        '''
        Constructor
        '''
        self.schema_version = 1
        self._filters = None
        self._queue_manager = None
        super(EngineDAO, self).__init__(db)
        self._filters = self.get_filters()
        self.reinit_processors()

    def _migrate_db(self, cursor, version):
        if (version < 1):
            self._migrate_table(cursor, 'States')
            cursor.execute(u"UPDATE States SET last_transfer = 'upload' WHERE last_local_updated < last_remote_updated AND folderish=0;")
            cursor.execute(u"UPDATE States SET last_transfer = 'download' WHERE last_local_updated > last_remote_updated AND folderish=0;")
            self.update_config(SCHEMA_VERSION, 1)
        if (version < 2):
            cursor.execute("CREATE TABLE if not exists ToRemoteScan(path STRING NOT NULL, PRIMARY KEY(path))")
            self.update_config(SCHEMA_VERSION, 2)

    def _create_state_table(self, cursor, force=False):
        if force:
            statement = ''
        else:
            statement = 'if not exists '
        cursor.execute("CREATE TABLE "+statement+"States(id INTEGER NOT NULL, last_local_updated TIMESTAMP,"
          + "last_remote_updated TIMESTAMP, local_digest VARCHAR, remote_digest VARCHAR, local_path VARCHAR,"
          + "remote_ref VARCHAR, local_parent_path VARCHAR, remote_parent_ref VARCHAR, remote_parent_path VARCHAR,"
          + "local_name VARCHAR, remote_name VARCHAR, size INTEGER DEFAULT (0), folderish INTEGER, local_state VARCHAR DEFAULT('unknown'), remote_state VARCHAR DEFAULT('unknown'),"
          + "pair_state VARCHAR DEFAULT('unknown'), remote_can_rename INTEGER, remote_can_delete INTEGER, remote_can_update INTEGER,"
          + "remote_can_create_child INTEGER, last_remote_modifier VARCHAR,"
          + "last_sync_date TIMESTAMP, error_count INTEGER DEFAULT (0), last_sync_error_date TIMESTAMP, last_error VARCHAR, last_error_details TEXT, version INTEGER DEFAULT (0), processor INTEGER DEFAULT (0), last_transfer VARCHAR, PRIMARY KEY (id));")

    def _init_db(self, cursor):
        super(EngineDAO, self)._init_db(cursor)
        cursor.execute("CREATE TABLE if not exists Filters(path STRING NOT NULL, PRIMARY KEY(path))")
        cursor.execute("CREATE TABLE if not exists RemoteScan(path STRING NOT NULL, PRIMARY KEY(path))")
        cursor.execute("CREATE TABLE if not exists ToRemoteScan(path STRING NOT NULL, PRIMARY KEY(path))")
        self._create_state_table(cursor)

    def _get_read_connection(self, factory=StateRow):
        return super(EngineDAO, self)._get_read_connection(factory)

    def release_processor(self, processor_id):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            # TO_REVIEW Might go back to primary key id
            c.execute("UPDATE States SET processor=0 WHERE processor=?", (processor_id,))
            if self.auto_commit:
                con.commit()
            log.trace('Released processor %d', processor_id)
        finally:
            self._lock.release()
        return c.rowcount == 1

    def acquire_processor(self, thread_id, row_id):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET processor=? WHERE id=? AND processor=0", (thread_id, row_id))
            if self.auto_commit:
                con.commit()
            log.trace('Acquired processor %d for row %d', thread_id, row_id)
        finally:
            self._lock.release()
        return c.rowcount == 1

    def reinit_states(self):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("DROP TABLE States")
            self._create_state_table(c, force=True)
            con.commit()
            log.trace("Vacuum sqlite")
            con.execute("VACUUM")
            log.trace("Vacuum sqlite finished")
        finally:
            self._lock.release()

    def reinit_processors(self):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET processor=0")
            c.execute("UPDATE States SET error_count=0, last_sync_error_date=NULL, last_error = NULL WHERE pair_state='synchronized'")
            if self.auto_commit:
                con.commit()
            log.trace("Vacuum sqlite")
            con.execute("VACUUM")
            log.trace("Vacuum sqlite finished")
        finally:
            self._lock.release()

    def delete_remote_state(self, doc_pair):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            update = "UPDATE States SET remote_state='deleted', pair_state=?"
            c.execute(update + " WHERE id=?", ('remotely_deleted',doc_pair.id))
            if doc_pair.folderish:
                # TO_REVIEW New state recursive_remotely_deleted
                c.execute(update + self._get_recursive_condition(doc_pair), ('parent_remotely_deleted',))
            # Only queue parent
            self._queue_pair_state(doc_pair.id, doc_pair.folderish, 'remotely_deleted')
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def delete_local_state(self, doc_pair):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            # Check parent to see current pair state
            parent = c.execute("SELECT * FROM States WHERE local_path=?", (doc_pair.local_parent_path,)).fetchone()
            if parent is not None and (parent.pair_state == 'locally_deleted' or parent.pair_state == 'parent_locally_deleted'):
                current_state = 'parent_locally_deleted'
            else:
                current_state = 'locally_deleted'
            update = "UPDATE States SET local_state='deleted', pair_state=?"
            c.execute(update + " WHERE id=?", (current_state, doc_pair.id))
            if doc_pair.folderish:
                # TO_REVIEW New state recursive_locally_deleted
                c.execute(update + self._get_recursive_condition(doc_pair), ('parent_locally_deleted',))
            # Only queue parent
            if current_state == "locally_deleted":
                self._queue_pair_state(doc_pair.id, doc_pair.folderish, current_state)
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def insert_local_state(self, info, parent_path):
        pair_state = PAIR_STATES.get(('created', 'unknown'))
        digest = info.get_digest()
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            name = os.path.basename(info.path)
            c.execute("INSERT INTO States(last_local_updated, local_digest, "
                      + "local_path, local_parent_path, local_name, folderish, size, local_state, remote_state, pair_state)"
                      + " VALUES(?,?,?,?,?,?,?,'created','unknown',?)", (info.last_modification_time, digest, info.path,
                                                    parent_path, name, info.folderish, info.size, pair_state))
            row_id = c.lastrowid
            parent = c.execute("SELECT * FROM States WHERE local_path=?", (parent_path,)).fetchone()
            # Dont queue if parent is not yet created
            if (parent is None and parent_path == '') or (parent is not None and parent.pair_state != "locally_created"):
                self._queue_pair_state(row_id, info.folderish, pair_state)
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()
        return row_id

    def get_last_files(self, number, direction=""):
        c = self._get_read_connection(factory=StateRow).cursor()
        condition = ""
        if direction == "remote":
            condition = " AND last_transfer = 'upload'"
        elif direction == "local":
            condition = " AND last_transfer = 'download'"
        return c.execute("SELECT * FROM States WHERE pair_state='synchronized' AND folderish=0" + condition + " ORDER BY last_sync_date DESC LIMIT " + str(number)).fetchall()

    def _get_to_sync_condition(self):
        return "pair_state != 'synchronized' AND pair_state != 'unsynchronized'"

    def register_queue_manager(self, manager):
        # Prevent any update while init queue
        self._lock.acquire()
        con = None
        try:
            self._queue_manager = manager
            con = self._get_write_connection(factory=StateRow)
            c = con.cursor()
            # Order by path to be sure to process parents before childs
            pairs = c.execute("SELECT * FROM States WHERE " + self._get_to_sync_condition() + " ORDER BY local_path ASC").fetchall()
            folders = dict()
            for pair in pairs:
                # Add all the folders
                if pair.folderish:
                    folders[pair.local_path] = True
                if not  pair.local_parent_path in folders:
                    self._queue_manager.push_ref(pair.id, pair.folderish, pair.pair_state)
        # Dont block everything if queue manager fail
        # TODO As the error should be fatal not sure we need this
        finally:
            self._lock.release()

    def _queue_pair_state(self, row_id, folderish, pair_state, pair=None):
        if (self._queue_manager is not None
             and pair_state != 'synchronized' and pair_state != 'unsynchronized'):
            if pair_state == 'conflicted':
                log.trace("Emit newConflict with: %r: %r", row_id, pair)
                self.newConflict.emit(row_id)
            else:
                log.trace("Push to queue: %s: %r", pair_state, pair)
                self._queue_manager.push_ref(row_id, folderish, pair_state)
        else:
            log.trace("Will not push pair: %s: %r", pair_state, pair)
        return

    def _get_pair_state(self, row):
        return PAIR_STATES.get((row.local_state, row.remote_state))

    def update_last_transfer(self, row_id, transfer):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET last_transfer=? WHERE id=?", (row_id, transfer))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def update_local_state(self, row, info, versionned=True, queue=True):
        pair_state = self._get_pair_state(row)
        version = ''
        if versionned:
            version = ', version=version+1'
            log.trace('Increasing version to %d for pair %r', row.version + 1, info)
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            # Should not update this
            c.execute("UPDATE States SET last_local_updated=?, local_digest=?, local_path=?, local_parent_path=?, local_name=?,"
                      + "local_state=?, size=?, remote_state=?, pair_state=?" + version +
                      " WHERE id=?", (info.last_modification_time, row.local_digest, info.path, os.path.dirname(info.path),
                                        os.path.basename(info.path), row.local_state, info.size, row.remote_state,
                                        pair_state, row.id))
            if row.pair_state != pair_state and queue:
                self._queue_pair_state(row.id, info.folderish, pair_state, row)
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def get_valid_duplicate_file(self, digest):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE remote_digest=? AND pair_state='synchronized'", (digest,)).fetchone()

    def get_remote_children(self, ref):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE remote_parent_ref=?", (ref,)).fetchall()

    def get_conflict_count(self):
        return self.get_count("pair_state='conflicted'")

    def get_error_count(self, threshold=3):
        return self.get_count("error_count > " + str(threshold))

    def get_syncing_count(self):
        query = "pair_state!='synchronized' AND pair_state!='conflicted' AND pair_state!='unsynchronized'"
        return self.get_count(query)

    def get_sync_count(self, filetype=None):
        query = "pair_state='synchronized'"
        if filetype == "file":
            query = query + " AND folderish=0"
        elif filetype == "folder":
            query = query + " AND folderish=1"
        return self.get_count(query)

    def get_count(self, condition=None):
        query = "SELECT COUNT(*) as count FROM States"
        if condition is not None:
            query = query + " WHERE " + condition
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute(query).fetchone().count

    def get_global_size(self):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT SUM(size) as sum FROM States WHERE pair_state='synchronized'").fetchone().sum

    def get_conflicts(self):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE pair_state='conflicted'").fetchall()

    def get_errors(self, limit=3):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE error_count>?", (limit,)).fetchall()

    def get_local_children(self, path):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE local_parent_path=?", (path,)).fetchall()

    def get_states_from_partial_local(self, path):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE local_path LIKE ?", (path + '%',)).fetchall()

    def get_first_state_from_partial_remote(self, ref):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE remote_ref LIKE ? ORDER BY last_remote_updated ASC LIMIT 1",
                         ('%' + ref,)).fetchone()

    def get_normal_state_from_remote(self, ref):
        # TODO Select the only states that is not a collection
        states = self.get_states_from_remote(ref)
        if len(states) == 0:
            return None
        return states[0]

    def get_state_from_remote_with_path(self, ref, path):
        # remote_path root is empty, should refactor this
        if path == '/':
            path = ""
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE remote_ref=? AND remote_parent_path=?", (ref,path)).fetchone()

    def get_states_from_remote(self, ref):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE remote_ref=?", (ref,)).fetchall()

    def get_state_from_id(self, row_id, from_write=False):
        # Dont need to read from write as auto_commit is True
        if from_write and self.auto_commit:
            from_write = False
        try:
            if from_write:
                self._lock.acquire()
                c = self._get_write_connection(factory=StateRow).cursor()
            else:
                c = self._get_read_connection(factory=StateRow).cursor()
            state = c.execute("SELECT * FROM States WHERE id=?", (row_id,)).fetchone()
        finally:
            if from_write:
                self._lock.release()
        return state

    def _get_recursive_condition(self, doc_pair):
        return (" WHERE local_parent_path LIKE '" + self._escape(doc_pair.local_path) + "/%'"
                    + " OR local_parent_path = '" + self._escape(doc_pair.local_path) + "'")

    def update_remote_parent_path(self, doc_pair, new_path):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            if doc_pair.folderish:
                remote_path = doc_pair.remote_parent_path + "/" + doc_pair.remote_ref
                query = "UPDATE States SET remote_parent_path='%s/%s' || substr(remote_parent_path,%d)" % (
                    self._escape(new_path), self._escape(doc_pair.remote_ref), len(remote_path) + 1)
                query = query + self._get_recursive_condition(doc_pair)
                log.trace("Update remote_parent_path: " + query)
                c.execute(query)
            c.execute("UPDATE States SET remote_parent_path=? WHERE id=?", (new_path, doc_pair.id))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def update_local_paths(self, doc_pair):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET local_parent_path=?, local_path=? WHERE id=?", (doc_pair.local_parent_path, doc_pair.local_path, doc_pair.id))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def update_local_parent_path(self, doc_pair, new_name, new_path):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            if doc_pair.folderish:
                if new_path == '/':
                    new_path = ''
                escaped_new_path = self._escape(new_path)
                escaped_new_name = self._escape(new_name)
                query = ("UPDATE States SET local_parent_path='%s/%s' || substr(local_parent_path,%d), local_path='%s/%s' || substr(local_path,%d)" %
                         (escaped_new_path, escaped_new_name, len(doc_pair.local_path) + 1,
                          escaped_new_path, escaped_new_name, len(doc_pair.local_path)+1))
                query = query + self._get_recursive_condition(doc_pair)
                c.execute(query)
            # Dont need to update the path as it is refresh later
            c.execute("UPDATE States SET local_parent_path=? WHERE id=?", (new_path, doc_pair.id))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def mark_descendants_remotely_deleted(self, doc_pair):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            update = "UPDATE States SET local_digest=NULL, last_local_updated=NULL, local_name=NULL, remote_state='deleted', pair_state='remotely_deleted'"
            c.execute(update + " WHERE id=?", (doc_pair.id,))
            if doc_pair.folderish:
                c.execute(update + self._get_recursive_condition(doc_pair))
            if self.auto_commit:
                con.commit()
            self._queue_pair_state(doc_pair.id, doc_pair.folderish, doc_pair.pair_state)
        finally:
            self._lock.release()

    def mark_descendants_remotely_created(self, doc_pair):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            update = "UPDATE States SET local_digest=NULL, last_local_updated=NULL, local_name=NULL, remote_state='created', pair_state='remotely_created'"
            c.execute(update + " WHERE id=" + str(doc_pair.id))
            if doc_pair.folderish:
                c.execute(update + self._get_recursive_condition(doc_pair))
            if self.auto_commit:
                con.commit()
            self._queue_pair_state(doc_pair.id, doc_pair.folderish, doc_pair.pair_state)
        finally:
            self._lock.release()

    def mark_descendants_locally_created(self, doc_pair):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            update = "UPDATE States SET remote_digest=NULL, remote_ref=NULL, remote_parent_ref=NULL, remote_parent_path=NULL, last_remote_updated=NULL, remote_name=NULL, remote_state='unknown', local_state='created', pair_state='locally_created'"
            c.execute(update + " WHERE id=" + str(doc_pair.id))
            if doc_pair.folderish:
                c.execute(update + self._get_recursive_condition(doc_pair))
            if self.auto_commit:
                con.commit()
            self._queue_pair_state(doc_pair.id, doc_pair.folderish, doc_pair.pair_state)
        finally:
            self._lock.release()

    def remove_state(self, doc_pair):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("DELETE FROM States WHERE id=?", (doc_pair.id,))
            if doc_pair.folderish:
                c.execute("DELETE FROM States" + self._get_recursive_condition(doc_pair))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def get_state_from_local(self, path):
        c = self._get_read_connection(factory=StateRow).cursor()
        return c.execute("SELECT * FROM States WHERE local_path=?", (path,)).fetchone()

    def insert_remote_state(self, info, remote_parent_path, local_path, local_parent_path):
        pair_state = PAIR_STATES.get(('unknown','created'))
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("INSERT INTO States (remote_ref, remote_parent_ref, " +
                      "remote_parent_path, remote_name, last_remote_updated, remote_can_rename," +
                      "remote_can_delete, remote_can_update, " +
                      "remote_can_create_child, last_remote_modifier, remote_digest," +
                      "folderish, last_remote_modifier, local_path, local_parent_path, remote_state, local_state, pair_state)" +
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'created','unknown',?)",
                      (info.uid, info.parent_uid, remote_parent_path, info.name,
                       info.last_modification_time, info.can_rename, info.can_delete, info.can_update,
                       info.can_create_child, info.last_contributor, info.digest, info.folderish, info.last_contributor,
                       local_path, local_parent_path, pair_state))
            row_id = c.lastrowid
            if self.auto_commit:
                con.commit()
            # Check if parent is not in creation
            parent = c.execute("SELECT * FROM States WHERE local_path=?", (local_parent_path,)).fetchone()
            if (parent is None and local_parent_path == '') or (parent is not None and parent.pair_state != "remotely_created"):
                self._queue_pair_state(row_id, info.folderish, pair_state)
        finally:
            self._lock.release()
        return row_id

    def queue_children(self, row):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            children = c.execute("SELECT * FROM States WHERE remote_parent_ref=? or local_parent_path=? AND " +
                                    self._get_to_sync_condition(), (row.remote_ref, row.local_path)).fetchall()
            log.debug("Queuing %d children of '%r'", len(children), row)
            for child in children:
                self._queue_pair_state(child.id, child.folderish, child.pair_state)
        finally:
            self._lock.release()

    def increase_error(self, row, error, details=None, incr=1):
        error_date = datetime.utcnow()
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET last_error=?, last_sync_error_date=?, error_count = error_count + ?, last_error_details=? " +
                      "WHERE id=?", (error, error_date, incr, details, row.id))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()
        row.last_error = error
        row.error_count = row.error_count + incr
        row.last_sync_error_date = error_date

    def reset_error(self, row):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET last_error=NULL, last_error_details=NULL, last_sync_error_date=NULL, error_count = 0" +
                      " WHERE id=?", (row.id,))
            if self.auto_commit:
                con.commit()
            self._queue_pair_state(row.id, row.folderish, row.pair_state)
        finally:
            self._lock.release()
        row.last_error = None
        row.error_count = 0
        row.last_sync_error_date = None

    def force_remote(self, row):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET local_state='synchronized', remote_state='modified', pair_state='remotely_modified', last_error=NULL, last_sync_error_date=NULL, error_count = 0" +
                      " WHERE id=? AND version=?", (row.id, row.version))
            self._queue_pair_state(row.id, row.folderish, "remotely_modified")
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()
        return c.rowcount == 1

    def force_local(self, row):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET local_state='created', remote_state='unknown', pair_state='locally_created', last_error=NULL, last_sync_error_date=NULL, error_count = 0" +
                      " WHERE id=? AND version=?", (row.id, row.version))
            self._queue_pair_state(row.id, row.folderish, "locally_created")
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()
        return c.rowcount == 1

    def set_conflict_state(self, row):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET pair_state='conflicted' WHERE id=?",
                      (row.id, ))
            self.newConflict.emit(row.id)
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()
        return c.rowcount == 1

    def synchronize_state(self, row, version=None, state='synchronized'):
        if version is None:
            version = row.version
        log.trace('Try to synchronize state for [local_path=%s, remote_name=%s, version=%s] with version=%s',
                  row.local_path, row.remote_name, row.version, version)
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET local_state='synchronized', remote_state='synchronized', " +
                      "pair_state=?, last_sync_date=?, processor = 0, last_error=NULL, error_count=0, last_sync_error_date=NULL " +
                      "WHERE id=? and version=?",
                      (state, datetime.utcnow(), row.id, version))
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()
        result = c.rowcount == 1
        # Retry without version for folder
        if not result and state == 'synchronized' and row.folderish:
            self._lock.acquire()
            try:
                con = self._get_write_connection()
                c = con.cursor()
                c.execute("UPDATE States SET local_state='synchronized', remote_state='synchronized', " +
                          "pair_state=?, last_sync_date=?, processor = 0, last_error=NULL, error_count=0, last_sync_error_date=NULL " +
                          "WHERE id=? and local_path=? and remote_name=? and remote_ref=? and remote_parent_ref=?",
                          (state, datetime.utcnow(), row.id, row.local_path, row.remote_name, row.remote_ref, row.remote_parent_ref))
                if self.auto_commit:
                    con.commit()
            finally:
                self._lock.release()
            result = c.rowcount == 1
        if not result:
            log.trace("Was not able to synchronize state: %r", row)
        elif row.folderish and state == 'synchronized':
            self.queue_children(row)
        return result

    def update_remote_state(self, row, info, remote_parent_path=None, versionned=True):
        pair_state = self._get_pair_state(row)
        if remote_parent_path is None:
            remote_parent_path = row.remote_parent_path
        version = ''
        if versionned:
            version = ', version=version+1'
            log.trace('Increasing version to %d for pair %r', row.version + 1, info)
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("UPDATE States SET remote_ref=?, remote_parent_ref=?, " +
                      "remote_parent_path=?, remote_name=?, last_remote_updated=?, remote_can_rename=?," +
                      "remote_can_delete=?, remote_can_update=?, " +
                      "remote_can_create_child=?, last_remote_modifier=?, remote_digest=?, local_state=?," +
                      "remote_state=?, pair_state=?, last_remote_modifier=?" + version + " WHERE id=?",
                      (info.uid, info.parent_uid, remote_parent_path, info.name,
                       info.last_modification_time, info.can_rename, info.can_delete, info.can_update,
                       info.can_create_child, info.last_contributor, info.digest, row.local_state,
                       row.remote_state, pair_state, info.last_contributor, row.id))
            if self.auto_commit:
                con.commit()
            if row.pair_state != pair_state:
                self._queue_pair_state(row.id, info.folderish, pair_state)
        finally:
            self._lock.release()

    def _clean_filter_path(self, path):
        if not path.endswith("/"):
            path = path + "/"
        return path

    def add_path_to_scan(self, path):
        path = self._clean_filter_path(path)
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            # Remove any subchilds as it is gonna be scanned anyway
            c.execute("DELETE FROM ToRemoteScan WHERE path LIKE ?", (path+'%',))
            # ADD IT
            c.execute("INSERT INTO ToRemoteScan(path) VALUES(?)", (path,))
            if self.auto_commit:
                con.commit()
        except sqlite3.IntegrityError:
            pass
        finally:
            self._lock.release()

    def delete_path_to_scan(self, path):
        path = self._clean_filter_path(path)
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            # ADD IT
            c.execute("DELETE FROM ToRemoteScan WHERE path=?", (path,))
            if self.auto_commit:
                con.commit()
        except sqlite3.IntegrityError:
            pass
        finally:
            self._lock.release()

    def get_paths_to_scan(self):
        c = self._get_read_connection().cursor()
        return c.execute(u"SELECT * FROM ToRemoteScan").fetchall()

    def add_path_scanned(self, path):
        path = self._clean_filter_path(path)
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            # ADD IT
            c.execute("INSERT INTO RemoteScan(path) VALUES(?)", (path,))
            if self.auto_commit:
                con.commit()
        except sqlite3.IntegrityError:
            pass
        finally:
            self._lock.release()

    def clean_scanned(self):
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("DELETE FROM RemoteScan")
            if self.auto_commit:
                con.commit()
        finally:
            self._lock.release()

    def is_path_scanned(self, path):
        path = self._clean_filter_path(path)
        c = self._get_read_connection().cursor()
        row = c.execute("SELECT COUNT(path) FROM RemoteScan WHERE path=? LIMIT 1", (path,)).fetchone()
        return row[0] > 0

    def get_previous_sync_file(self, ref, sync_mode=None):
        mode_condition = ""
        if sync_mode is not None:
            mode_condition = "AND last_transfer='" + sync_mode + "' "
        state = self.get_normal_state_from_remote(ref)
        if state is None:
            return None
        c = self._get_read_connection().cursor()
        self.get_syncing_count()
        return c.execute(u"SELECT * FROM States WHERE last_sync_date>? " + mode_condition + self.get_batch_sync_ignore() + " ORDER BY last_sync_date ASC LIMIT 1", (state.last_sync_date,)).fetchone()

    def get_batch_sync_ignore(self):
        return "AND ( pair_state != 'unsynchronized' AND pair_state != 'conflicted') AND folderish=0 "

    def get_next_sync_file(self, ref, sync_mode=None):
        mode_condition = ""
        if sync_mode is not None:
            mode_condition = "AND last_transfer='" + sync_mode + "' "
        state = self.get_normal_state_from_remote(ref)
        if state is None:
            return None
        c = self._get_read_connection().cursor()
        return c.execute(u"SELECT * FROM States WHERE last_sync_date<? " + mode_condition + self.get_batch_sync_ignore() + " ORDER BY last_sync_date DESC LIMIT 1", (state.last_sync_date,)).fetchone()

    def get_next_folder_file(self, ref):
        state = self.get_normal_state_from_remote(ref)
        c = self._get_read_connection().cursor()
        return c.execute("SELECT * FROM States WHERE remote_parent_ref=? AND remote_name > ? AND folderish=0 ORDER BY remote_name ASC LIMIT 1", (state.remote_parent_ref,state.remote_name)).fetchone()

    def get_previous_folder_file(self, ref):
        state = self.get_normal_state_from_remote(ref)
        c = self._get_read_connection().cursor()
        return c.execute("SELECT * FROM States WHERE remote_parent_ref=? AND remote_name < ? AND folderish=0 ORDER BY remote_name DESC LIMIT 1", (state.remote_parent_ref,state.remote_name)).fetchone()

    def is_filter(self, path):
        path = self._clean_filter_path(path)
        if any([path.startswith(filter_obj.path) for filter_obj in self._filters]):
            return True
        else:
            return False

    def get_filters(self):
        c = self._get_read_connection().cursor()
        return c.execute("SELECT * FROM Filters").fetchall()

    def add_filter(self, path):
        if self.is_filter(path):
            return
        path = self._clean_filter_path(path)
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            # DELETE ANY SUBFILTERS
            c.execute("DELETE FROM Filters WHERE path LIKE ?", (path + '%',))
            # PREVENT ANY RESCAN
            c.execute("DELETE FROM ToRemoteScan WHERE path LIKE ?", (path+'%',))
            # ADD IT
            c.execute("INSERT INTO Filters(path) VALUES(?)", (path,))
            # TODO ADD THIS path AS remotely_deleted
            if self.auto_commit:
                con.commit()
            self._filters = self.get_filters()
        finally:
            self._lock.release()

    def remove_filter(self, path):
        path = self._clean_filter_path(path)
        self._lock.acquire()
        try:
            con = self._get_write_connection()
            c = con.cursor()
            c.execute("DELETE FROM Filters WHERE path LIKE ?", (path + '%',))
            if self.auto_commit:
                con.commit()
            self._filters = self.get_filters()
        finally:
            self._lock.release()

    def _escape(self, _str):
        return _str.replace("'", "''")
