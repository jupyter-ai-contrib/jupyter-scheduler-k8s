"""
Notebook executor for K8s jobs.
Reads notebook from NOTEBOOK_PATH, executes it, saves to OUTPUT_PATH.
Supports S3 storage for durability across cluster failures.
"""

import os
import sys
import json
import logging
import shutil
import subprocess
from pathlib import Path

import nbformat
import nbconvert
from nbconvert.preprocessors import ExecutePreprocessor, CellExecutionError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Execute a notebook based on environment variables."""
    # S3 configuration (optional)
    s3_input_prefix = os.environ.get('S3_INPUT_PREFIX')
    s3_output_prefix = os.environ.get('S3_OUTPUT_PREFIX')
    
    # Standard configuration
    notebook_path = os.environ.get('NOTEBOOK_PATH')
    output_path = os.environ.get('OUTPUT_PATH')
    parameters_json = os.environ.get('PARAMETERS', '{}')
    kernel_name = os.environ.get('KERNEL_NAME', 'python3')
    timeout = int(os.environ.get('TIMEOUT', '600'))
    package_input_folder = os.environ.get('PACKAGE_INPUT_FOLDER', 'false').lower() == 'true'
    output_formats_json = os.environ.get('OUTPUT_FORMATS', '[]')
    
    # If S3 is configured, download inputs first
    if s3_input_prefix:
        logger.info("S3 mode: downloading inputs from S3")
        local_input_dir = '/tmp/inputs'
        download_from_s3(s3_input_prefix, local_input_dir)
        
        # Update paths to use downloaded files
        notebook_name = Path(notebook_path).name
        notebook_path = str(Path(local_input_dir) / notebook_name)
        
        # Update output path to local temp directory
        output_name = Path(output_path).name
        local_output_dir = '/tmp/outputs'
        Path(local_output_dir).mkdir(parents=True, exist_ok=True)
        output_path = str(Path(local_output_dir) / output_name)
    
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
    
    try:
        output_formats = json.loads(output_formats_json)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid OUTPUT_FORMATS JSON: {e}")
        sys.exit(1)
    
    if package_input_folder:
        if s3_input_prefix:
            # In S3 mode, all input files are already in local_input_dir
            execution_path = notebook_path
            execution_dir = str(Path(notebook_path).parent.absolute())
        else:
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
    
    generate_output_formats(nb, output_path, output_formats)
    
    if s3_output_prefix:
        logger.info("S3 mode: uploading outputs to S3")
        upload_to_s3(local_output_dir, s3_output_prefix)


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


def generate_output_formats(nb, output_path, requested_formats):
    """Generate only the requested output formats from the executed notebook.
    
    This mirrors jupyter-scheduler's approach in create_output_files method.
    Only generates formats that were specifically requested.
    """
    output_dir = Path(output_path).parent
    base_name = Path(output_path).stem
    
    for output_format in requested_formats:
        if output_format == 'ipynb':
            # Already saved as the main output
            continue
            
        try:
            exporter_class = nbconvert.get_exporter(output_format)
            exporter = exporter_class()
            
            output, _ = exporter.from_notebook_node(nb)
            
            output_file = output_dir / f"{base_name}.{output_format}"
            
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(output)
                    
            logger.info(f"{output_format.upper()} output saved: {output_file}")
            
        except Exception as e:
            logger.warning(f"Failed to generate {output_format.upper()} output: {e}")


def download_from_s3(s3_prefix, local_dir):
    """Download files from S3 to local directory."""
    logger.info(f"Downloading from {s3_prefix} to {local_dir}")
    
    # Create local directory if it doesn't exist
    Path(local_dir).mkdir(parents=True, exist_ok=True)
    
    # Use AWS CLI to sync from S3
    cmd = ['aws', 's3', 'sync', s3_prefix, local_dir, '--quiet']
    
    # Add endpoint URL if specified (for S3-compatible storage like MinIO)
    endpoint_url = os.environ.get('S3_ENDPOINT_URL')
    if endpoint_url:
        cmd.extend(['--endpoint-url', endpoint_url])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"S3 download failed: {result.stderr}")
        sys.exit(1)
    
    logger.info("S3 download completed")


def upload_to_s3(local_dir, s3_prefix):
    """Upload files from local directory to S3."""
    logger.info(f"Uploading from {local_dir} to {s3_prefix}")
    
    # Use AWS CLI to sync to S3
    cmd = ['aws', 's3', 'sync', local_dir, s3_prefix, '--quiet']
    
    # Add endpoint URL if specified
    endpoint_url = os.environ.get('S3_ENDPOINT_URL')
    if endpoint_url:
        cmd.extend(['--endpoint-url', endpoint_url])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"S3 upload failed: {result.stderr}")
        sys.exit(1)
    
    logger.info("S3 upload completed")

if __name__ == "__main__":
    main()
