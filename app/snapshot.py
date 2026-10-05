"""Disk-backed file inventory: every discovered regular file, queryable in pages."""
from pathlib import Path
from contextlib import contextmanager
import csv,io,json,sqlite3

class Inventory:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.executescript('''CREATE TABLE files(path TEXT PRIMARY KEY,parent TEXT,name TEXT,size INTEGER,allocated INTEGER,extension TEXT,modified REAL,fingerprint TEXT,device TEXT,inode TEXT);
        CREATE INDEX files_parent_size ON files(parent,size DESC,path);
        CREATE INDEX files_size ON files(size);
        CREATE TABLE identities(device TEXT,inode TEXT,PRIMARY KEY(device,inode));
        CREATE TABLE directories(path TEXT PRIMARY KEY,parent TEXT,name TEXT,size INTEGER);
        CREATE INDEX directories_parent ON directories(parent);
        CREATE TABLE errors(path TEXT,reason TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);''')
        self.pending=0
    def identity(self,info):
        cursor=self.db.execute('INSERT OR IGNORE INTO identities VALUES(?,?)',(str(info.st_dev),str(info.st_ino)))
        return cursor.rowcount>0
    def file(self,path,info,allocated,fingerprint):
        self.db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?)',(str(path),str(path.parent),path.name,info.st_size,allocated,path.suffix.lower() or '(无扩展名)',info.st_mtime,fingerprint,str(info.st_dev),str(info.st_ino)))
        self.pending+=1
        if self.pending>=5000:self.db.commit();self.pending=0
    def error(self,path,error):self.db.execute('INSERT INTO errors VALUES(?,?)',(str(path),str(error)[:500]))
    def finish(self,result,sizes):
        self.db.executemany('INSERT INTO directories VALUES(?,?,?,?)',[(str(p),str(p.parent),p.name,n) for p,n in sizes.items()])
        summary={k:v for k,v in result.items() if not k.startswith('_')}
        self.db.execute('INSERT INTO metadata VALUES(?,?)',('summary',json.dumps(summary,ensure_ascii=False)))
        self.db.commit();self.db.close()
    def close(self):self.db.close()

@contextmanager
def connect(path):
    connection=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    try:yield connection
    finally:connection.close()

def summary(path):
    with connect(path) as db:
        row=db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()
        return json.loads(row[0]) if row else None

def browse(path,selected,query='',offset=0,limit=100,recursive=False,sort='size'):
    with connect(path) as db:
        directory=db.execute('SELECT * FROM directories WHERE path=?',(selected,)).fetchone()
        if not directory:raise KeyError(selected)
        file_where='instr(lower(path),lower(?))>0' if recursive else 'parent=? AND instr(lower(name),lower(?))>0'
        args=[query] if recursive else [selected,query]
        directories=[] if recursive else [dict(r)|{'kind':'directory','allocated':None} for r in db.execute('SELECT path,parent,name,size FROM directories WHERE parent=? AND path!=? AND instr(lower(name),lower(?))>0',(selected,selected,query))]
        total=db.execute('SELECT count(*) FROM files WHERE '+file_where,args).fetchone()[0]+len(directories)
        order={'size':'size DESC,path','allocated':'coalesce(allocated,-1) DESC,path','name':'name COLLATE NOCASE,path','modified':'modified DESC,path'}[sort]
        # SQL union makes directories and files share one stable paginated ordering.
        if not recursive:
            rows=db.execute('SELECT path,name,size,allocated,extension,modified,kind FROM (SELECT path,name,size,allocated,extension,modified,\'file\' kind FROM files WHERE '+file_where+' UNION ALL SELECT path,name,size,NULL allocated,\'\' extension,NULL modified,\'directory\' kind FROM directories WHERE parent=? AND path!=? AND instr(lower(name),lower(?))>0) ORDER BY '+order+' LIMIT ? OFFSET ?',args+[selected,selected,query,limit,offset])
        else:rows=db.execute("SELECT path,name,size,allocated,extension,modified,'file' kind FROM files WHERE "+file_where+' ORDER BY '+order+' LIMIT ? OFFSET ?',args+[limit,offset])
        return {'items':[dict(r) for r in rows],'total':total,'offset':offset,'limit':limit,'has_more':offset+limit<total,'size':directory['size']}

def export_csv(path):
    output=io.StringIO();writer=csv.writer(output);writer.writerow(['path','logical_bytes','allocated_bytes','modified_epoch','extension'])
    yield '\ufeff'+output.getvalue()
    with connect(path) as db:
        for row in db.execute('SELECT path,size,allocated,modified,extension FROM files ORDER BY path'):
            output.seek(0);output.truncate(0)
            values=list(row)
            # Spreadsheet formula injection protection without altering inventory storage.
            if values[0].startswith(('=','+','-','@')):values[0]="'"+values[0]
            writer.writerow(values);yield output.getvalue()
