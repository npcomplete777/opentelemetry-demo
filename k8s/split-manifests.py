import yaml
import os
import re

# Read all manifests
with open('k8s/rendered/all-manifests.yaml', 'r') as f:
    content = f.read()

# Split on YAML document separator
docs = content.split('---')

# Track resources by component
components = {}

for doc in docs:
    if not doc.strip():
        continue
    try:
        manifest = yaml.safe_load(doc)
        if not manifest or 'kind' not in manifest:
            continue
        
        kind = manifest['kind']
        metadata = manifest.get('metadata', {})
        name = metadata.get('name', 'unknown')
        
        # Determine component name from labels or name
        labels = metadata.get('labels', {})
        component = labels.get('app.kubernetes.io/component') or \
                   labels.get('app.kubernetes.io/name') or \
                   name.split('-')[0]
        
        # Clean component name
        component = re.sub(r'[^a-z0-9-]', '', component.lower())
        if not component:
            component = 'common'
        
        if component not in components:
            components[component] = []
        
        components[component].append({
            'kind': kind,
            'name': name,
            'content': doc.strip()
        })
    except yaml.YAMLError:
        continue

# Write out component directories
base_dir = 'k8s/base'
os.makedirs(base_dir, exist_ok=True)

all_components = []

for component, resources in sorted(components.items()):
    comp_dir = os.path.join(base_dir, component)
    os.makedirs(comp_dir, exist_ok=True)
    
    # Write all resources for this component to one file
    with open(os.path.join(comp_dir, 'resources.yaml'), 'w') as f:
        for i, res in enumerate(resources):
            if i > 0:
                f.write('\n---\n')
            f.write(res['content'])
    
    # Create kustomization.yaml for this component
    with open(os.path.join(comp_dir, 'kustomization.yaml'), 'w') as f:
        f.write('apiVersion: kustomize.config.k8s.io/v1beta1\n')
        f.write('kind: Kustomization\n')
        f.write('resources:\n')
        f.write('  - resources.yaml\n')
    
    all_components.append(component)
    print(f"Created {component}: {len(resources)} resources")

# Create root kustomization.yaml
with open(os.path.join(base_dir, 'kustomization.yaml'), 'w') as f:
    f.write('apiVersion: kustomize.config.k8s.io/v1beta1\n')
    f.write('kind: Kustomization\n')
    f.write('resources:\n')
    for comp in sorted(all_components):
        f.write(f'  - {comp}\n')

print(f"\nTotal: {len(all_components)} components")
