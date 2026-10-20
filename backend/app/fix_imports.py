import re

def fix_imports(file_path):
    with open(file_path, 'r') as file:
        content = file.read()
    
    # Pattern to match the incorrect import lines
    # We are looking for lines like: from app.api.xxx = router as xxx_router
    # We want to replace ' = ' with ' import '
    # But note: we only want to fix the lines that have ' = router as '
    pattern = r'from app\.api\.(\w+) = router as (\w+_router)'
    replacement = r'from app.api.\1 import router as \2'
    
    # Apply the replacement
    fixed_content = re.sub(pattern, replacement, content)
    
    with open(file_path, 'w') as file:
        file.write(fixed_content)

if __name__ == '__main__':
    fix_imports(r'C:\solenos\backend\app\main.py')