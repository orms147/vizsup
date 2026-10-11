const fs = require('node:fs');
const lucide = require('C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/lucide');
const names = ['Captions','Play','Pause','ChevronRight','ChevronLeft','ChevronDown','Settings2','FolderOpen','Check','CheckCheck','Clock3','ArrowRight','ArrowLeft','Link','Plus','MoreHorizontal','Search','BookOpen','SlidersHorizontal','Volume2','VolumeX','Scissors','Merge','Trash2','Undo2','Redo2','LockKeyhole','UnlockKeyhole','CircleHelp','X','Download','Upload','CircleAlert','TriangleAlert','CircleCheck','Eye','EyeOff','Keyboard','Repeat2','Maximize2','Minimize2','PanelRightClose','AlignCenter','Type','Circle','FileVideo','LoaderCircle','Music2','Mic','RefreshCw','ShieldCheck','Square','ChevronUp','Replace','Copy','Sparkles','Focus','MoveHorizontal','MonitorPlay','KeyRound','HardDrive','Wifi','Dot'];
const result = {};
for(const name of names) { const data = lucide.icons[name]; if(!data) throw Error('Missing '+name); result[name]=data; }
fs.writeFileSync(__dirname+'/icons.js','window.VZ_ICONS='+JSON.stringify(result)+';\n');
console.log('Bundled '+names.length+' SVG icons.');
