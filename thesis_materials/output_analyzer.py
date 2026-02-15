import os
import json
from pathlib import Path

# Analyze your generation results
output_dir = "/content/code-codegen-aosp-llm-based/output"

def analyze_generation():
    results = {
        "total_files": 0,
        "llm_generated": 0,
        "template_based": 0,
        "file_sizes": [],
        "modules": []
    }
    
    # Count files
    for root, dirs, files in os.walk(output_dir):
        for file in files:
            if file.endswith(('.java', '.kt', '.cpp', '.py', '.xml', '.aidl')):
                results["total_files"] += 1
                filepath = os.path.join(root, file)
                size = os.path.getsize(filepath)
                results["file_sizes"].append({
                    "file": file,
                    "size": size,
                    "path": filepath
                })
    
    return results

# Run analysis
metrics = analyze_generation()
print(f"Total code files generated: {metrics['total_files']}")
print(f"Average file size: {sum([f['size'] for f in metrics['file_sizes']]) / len(metrics['file_sizes']) if metrics['file_sizes'] else 0:.0f} bytes")

# Save for thesis
with open('/content/thesis_materials/initial_metrics.json', 'w') as f:
    json.dump(metrics, f, indent=2)

print("✓ Metrics saved to thesis_materials/initial_metrics.json")