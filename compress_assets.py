import gzip
import os
import shutil

files = ["index.html", "style.css", "app.js"]

os.makedirs("micropython/web", exist_ok=True)

print("Pre-compressing web assets with gzip...")
for fname in files:
    src_path = os.path.join("web", fname)
    with open(src_path, "rb") as f_in:
        data = f_in.read()
    raw_size = len(data)
    
    gz_data = gzip.compress(data, compresslevel=9)
    gz_size = len(gz_data)
    
    gz_path = os.path.join("web", fname + ".gz")
    with open(gz_path, "wb") as f_out:
        f_out.write(gz_data)
        
    # Also sync to micropython/web/
    shutil.copy2(src_path, os.path.join("micropython/web", fname))
    shutil.copy2(gz_path, os.path.join("micropython/web", fname + ".gz"))
    
    ratio = (1 - (gz_size / raw_size)) * 100
    print(f"  {fname:12}: {raw_size:6} bytes -> {gz_size:6} bytes gz ({ratio:.1f}% reduction)")

print("Synced web assets to micropython/web/ successfully!")
