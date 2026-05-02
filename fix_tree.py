import os

path = r'e:\Profession\Projects\ODOO Projects\New SaaS for Odoo\odoo_saas_kit'
count = 0

for root, dirs, files in os.walk(path):
    for f in files:
        if f.endswith('.xml'):
            fp = os.path.join(root, f)
            with open(fp, 'r', encoding='utf-8') as fh:
                content = fh.read()
            
            new_content = content
            # Replace tree with list in view_mode values
            new_content = new_content.replace('>tree,form<', '>list,form<')
            new_content = new_content.replace('>tree,kanban,form,pivot,graph<', '>list,kanban,form,pivot,graph<')
            new_content = new_content.replace('>tree,form,kanban<', '>list,form,kanban<')
            new_content = new_content.replace('view_mode="tree,form"', 'view_mode="list,form"')
            new_content = new_content.replace('view_mode="tree"', 'view_mode="list"')
            
            if new_content != content:
                with open(fp, 'w', encoding='utf-8') as fh:
                    fh.write(new_content)
                count += 1
                print(f'FIXED: {os.path.relpath(fp, path)}')

print(f'\nTotal files fixed: {count}')
