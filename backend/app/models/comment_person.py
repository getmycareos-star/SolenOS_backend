import sys

file_path = 'C:\\solenos\\backend\\app\\models\\care.py'
with open(file_path, 'r') as f:
    lines = f.readlines()

# Find the start and end of the Person class
start = -1
end = -1
for i, line in enumerate(lines):
    if line.strip() == 'class Person(Base):':
        start = i
        break

if start != -1:
    # Look for the end of the class: next line that starts with 'class ' (not indented) or end of file
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == '':
            continue
        if stripped.startswith('class ') and not lines[i].startswith(' '):
            end = i
            break
    if end == -1:
        end = len(lines)
    # Comment out the lines from start to end-1
    for i in range(start, end):
        lines[i] = '# ' + lines[i]
    with open(file_path, 'w') as f:
        f.writelines(lines)
