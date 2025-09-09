// Entry point for JupyterLab extension
const plugins = require('./lib/index.js');
module.exports = plugins.default || plugins;