"""
Packaging utility to generate a clean, ready-to-upload submission.zip archive.
Excludes node_modules, venvs, and cache files.
"""

import os
import zipfile
import sys

EXCLUDE_DIRS = {
    "venv", ".venv", "node_modules", ".git", "__pycache__", "dist", ".cache"
}
EXCLUDE_EXTS = {
    ".pyc", ".pyo", ".pyd"
}

def create_submission_archive(output_filename="submission.zip"):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    zip_path = os.path.join(base_dir, output_filename)
    
    print(f"Creating clean submission archive: {zip_path}...")
    
    file_count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(base_dir):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            
            for file in files:
                if file == output_filename or file.endswith(tuple(EXCLUDE_EXTS)):
                    continue
                # Do not zip the raw dump.rdb if large
                if file == "dump.rdb":
                    continue
                    
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, base_dir)
                zipf.write(full_path, rel_path)
                file_count += 1
                
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"-> Archive successfully created with {file_count} files ({size_mb:.2f} MB).")
    print(f"-> Ready to upload: {zip_path}")
    return zip_path

if __name__ == "__main__":
    create_submission_archive()
