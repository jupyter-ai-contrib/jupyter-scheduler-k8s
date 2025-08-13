
"""
Notebook executor for K8s jobs.
Reads notebook from NOTEBOOK_PATH, executes it, saves to OUTPUT_PATH.
"""

import os
import sys
import json
import logging

import nbformat
from nbconvert.preprocessors import ExecutePreprocessor, CellExecutionError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Execute a notebook based on environment variables."""
    notebook_path = os.environ.get('NOTEBOOK_PATH')
    output_path = os.environ.get('OUTPUT_PATH')
    parameters_json = os.environ.get('PARAMETERS', '{}')
    kernel_name = os.environ.get('KERNEL_NAME', 'python3')
    timeout = int(os.environ.get('TIMEOUT', '600'))
    
    if not notebook_path:
        logger.error("NOTEBOOK_PATH environment variable is required")
        sys.exit(1)
    
    if not output_path:
        logger.error("OUTPUT_PATH environment variable is required")
        sys.exit(1)
    
    try:
        parameters = json.loads(parameters_json) if parameters_json else {}
    except json.JSONDecodeError as e:
        logger.error(f"Invalid PARAMETERS JSON: {e}")
        sys.exit(1)
    
    try:
        with open(notebook_path, 'r') as f:
            nb = nbformat.read(f, as_version=4)
    except Exception as e:
        logger.error(f"Failed to read notebook: {e}")
        sys.exit(1)
    
    logger.info(f"Executing notebook with kernel {kernel_name} (timeout: {timeout}s)")
    if parameters:
        logger.info(f"Parameters: {parameters}")
    
    if parameters:
        param_assignments = []
        for key, value in parameters.items():
            param_assignments.append(f"{key} = {json.dumps(value)}")
        
        param_code = "\n".join(param_assignments)
        param_cell = nbformat.v4.new_code_cell(source=param_code)
        param_cell.metadata["tags"] = ["injected-parameters"]
        
        insert_idx = 0
        for idx, cell in enumerate(nb.cells):
            if hasattr(cell, 'metadata') and "tags" in cell.metadata:
                if "parameters" in cell.metadata["tags"]:
                    insert_idx = idx + 1
                    break
        
        nb.cells.insert(insert_idx, param_cell)
    
    ep = ExecutePreprocessor(timeout=timeout, kernel_name=kernel_name)
    
    try:
        notebook_dir = os.path.dirname(os.path.abspath(notebook_path)) or '.'
        ep.preprocess(nb, {'metadata': {'path': notebook_dir}})
        logger.info("Execution completed successfully")
    except CellExecutionError as e:
        logger.error(f"Cell execution failed: {e}")
        logger.info("Saving partially executed notebook")
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        sys.exit(1)
    
    try:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        with open(output_path, 'w') as f:
            nbformat.write(nb, f)
        logger.info(f"Output saved: {output_path}")
    except Exception as e:
        logger.error(f"Failed to save output: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
