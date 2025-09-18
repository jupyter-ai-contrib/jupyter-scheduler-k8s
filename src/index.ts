import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { Scheduler } from '@jupyterlab/scheduler';
import K8sAdvancedOptions from './advanced-options';

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
    return K8sAdvancedOptions;
  }
};

const plugins: JupyterFrontEndPlugin<any>[] = [k8sAdvancedOptions];

export default plugins;