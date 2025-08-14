
"""
Notebook executor for K8s jobs.
Reads notebook from NOTEBOOK_PATH, executes it, saves to OUTPUT_PATH.
"""

import os
import sys
import json
import logging
import shutil
from pathlib import Path

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
    package_input_folder = os.environ.get('PACKAGE_INPUT_FOLDER', 'false').lower() == 'true'
    
    if not notebook_path:
        logger.error("NOTEBOOK_PATH environment variable is required")
        sys.exit(1)
    
    if not output_path:
        logger.error("OUTPUT_PATH environment variable is required")
        sys.exit(1)
    
    try:
        parameters = json.loads(parameters_json)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid PARAMETERS JSON: {e}")
        sys.exit(1)
    
    if package_input_folder:
        execution_path, execution_dir = copy_input_folder(notebook_path)
    else:
        execution_path = notebook_path
        execution_dir = str(Path(notebook_path).parent.absolute())
    
    try:
        with open(execution_path, 'r') as f:
            nb = nbformat.read(f, as_version=4)
    except Exception as e:
        logger.error(f"Failed to read notebook: {e}")
        sys.exit(1)
    
    logger.info(f"Executing notebook with kernel {kernel_name} (timeout: {timeout}s)")
    if parameters:
        logger.info(f"Parameters: {parameters}")
        inject_parameters(nb, parameters)
    
    execute_notebook(nb, execution_dir, kernel_name, timeout)
    save_notebook(nb, output_path)


def copy_input_folder(notebook_path):
    """Copy notebook directory to current working directory."""
    logger.info("Packaging input folder with notebook")
    source_dir = Path(notebook_path).parent
    work_dir = Path.cwd()
    
    shutil.copytree(source_dir, work_dir, dirs_exist_ok=True)
    
    notebook_name = Path(notebook_path).name
    execution_path = str(work_dir / notebook_name)
    execution_dir = str(work_dir)
    logger.info(f"Files copied to {work_dir}")
    
    return execution_path, execution_dir


def inject_parameters(nb, parameters):
    """Inject parameters into notebook as a new cell."""
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


def execute_notebook(nb, execution_dir, kernel_name, timeout):
    """Execute notebook and handle errors gracefully."""
    ep = ExecutePreprocessor(timeout=timeout, kernel_name=kernel_name)
    
    try:
        ep.preprocess(nb, {'metadata': {'path': execution_dir}})
        logger.info("Execution completed successfully")
    except CellExecutionError as e:
        logger.error(f"Cell execution failed: {e}")
        logger.info("Saving partially executed notebook")
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        sys.exit(1)


def save_notebook(nb, output_path):
    """Save notebook to output path, creating directories as needed."""
    try:
        output_dir = Path(output_path).parent
        if str(output_dir):
            output_dir.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            nbformat.write(nb, f)
        logger.info(f"Output saved: {output_path}")
    except Exception as e:
        logger.error(f"Failed to save output: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
