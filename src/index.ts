import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { Scheduler } from '@jupyterlab/scheduler';
import K8sAdvancedOptions from './advanced-options';

console.log('🚀 K8S EXTENSION: Loading jupyter-scheduler-k8s extension module');

/**
 * K8s advanced options plugin - provides override for default advanced options.
 * Uses the IAdvancedOptionsOverride token to cleanly override without conflicts.
 */
const k8sAdvancedOptions: JupyterFrontEndPlugin<Scheduler.IAdvancedOptions> = {
  id: 'jupyter-scheduler-k8s:advanced-options-override',
  autoStart: true,
  requires: [],
  provides: Scheduler.IAdvancedOptionsOverride,
  activate: (app: JupyterFrontEnd) => {
    console.log('🎯 K8S PLUGIN: Activating K8s advanced options override');
    console.log('   Providing token:', Scheduler.IAdvancedOptionsOverride);
    console.log('   Token name:', Scheduler.IAdvancedOptionsOverride?.name);
    console.log('   Returning:', K8sAdvancedOptions);
    return K8sAdvancedOptions;
  }
};

const plugins: JupyterFrontEndPlugin<any>[] = [k8sAdvancedOptions];

console.log('📦 K8S EXTENSION: Exporting plugins:', plugins);
console.log('   Plugin IDs:', plugins.map(p => p.id));

export default plugins;