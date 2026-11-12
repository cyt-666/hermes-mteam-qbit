(function () {
  'use strict';
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry || !sdk.fetchJSON) return;
  const R = sdk.React, h = R.createElement;
  const {useState, useEffect} = sdk.hooks;
  const base = '/api/plugins/mteam-qbit';
  const FRONTEND_VERSION = '0.3.6';
  const inputStyle = {padding:'9px',border:'1px solid #8886',borderRadius:'6px',background:'transparent',color:'inherit',width:'100%'};
  const btnStyle = {padding:'9px 15px',border:'1px solid #8888',borderRadius:'6px',marginRight:'8px',cursor:'pointer'};
  function Page() {
    const [businessBuild,setBusinessBuild] = useState('未报告'), [backendVersion,setBackendVersion] = useState('未知'), [v,setV] = useState(null), [hosts,setHosts] = useState('');
    const [secrets,setSecrets] = useState({mteam_api_key:'',qbit_username:'',qbit_password:''});
    const versionMismatch = backendVersion !== FRONTEND_VERSION;
    const [present,setPresent] = useState({}), [busy,setBusy] = useState(false), [message,setMessage] = useState('正在读取配置…');
    async function reload() {
      try {
        const d = await sdk.fetchJSON(base+'/settings');
        setBusinessBuild(typeof d.business_build==='string' && /^[a-f0-9]{64}$/.test(d.business_build)?d.business_build.slice(0,12):'未报告'); setV(d.settings); setBackendVersion(typeof d.backend_version==='string' && /^\d+\.\d+\.\d+$/.test(d.backend_version)?d.backend_version:'旧版或未报告');
        setHosts(d.settings.download_hosts.join('\n')); setPresent(d.secrets_set); setMessage('');
      } catch (_) { setMessage('读取失败。请确认插件已启用、当前 Profile 正确，且 Hermes 为 v0.23.1 兼容版本。'); }
    }
    useEffect(()=>{reload();},[]);
    function change(k,value) { setV(old=>({...old,[k]:value})); }
    function field(k,label,help,type) {
      return h('label',{key:k,style:{display:'block',marginBottom:'16px'}},
        h('div',{style:{fontWeight:600,marginBottom:'5px'}},label),
        h('input',{style:inputStyle,type:type||'text',value:v[k],disabled:busy,onChange:e=>change(k,type==='number'?Number(e.target.value):e.target.value)}),
        h('small',null,help));
    }
    async function save(e) {
      e.preventDefault(); if (busy || versionMismatch) return;
      setBusy(true);setMessage('正在保存…');
      try {
        await sdk.fetchJSON(base+'/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:{...v,download_hosts:hosts.split(/[\n,]/).map(x=>x.trim()).filter(Boolean)},secrets})});
        setSecrets({mteam_api_key:'',qbit_username:'',qbit_password:''});
        await reload(); setMessage('配置已保存。可以测试连接；新工具调用读取新配置。');
      } catch (_) {setMessage('保存失败。请检查地址、目录、有效期或管理权限；凭据可能已部分保存，刷新后检查。没有提交下载。');}
      finally {setBusy(false);}
    }
    async function test(target) {
      if(versionMismatch) return;
      setBusy(true);setMessage('正在测试已保存的配置，请稍候…');
      try {
        const result=await sdk.fetchJSON(base+'/test/'+target,{method:'POST'});
        if (!result || typeof result.ok!=='boolean' || typeof result.message!=='string') {
          setMessage('Dashboard 返回非预期响应；可能为旧后端或代理页面。请重启提供网页的进程并核对前后端版本。未确认馒头状态。');
        } else {
          let details='';
          const c=result.credential_diagnostics;
          if(c) {
            const bool=x=>x===true?'是':x===false?'否':'未知';
            details+=' 当前生效凭据存在：'+bool(c.effective_present)+'；当前 Profile 保存值存在：'+bool(c.profile_saved_present)+'；两者一致：'+bool(c.effective_matches_saved)+'。';
          }
          const d=result.http_diagnostics;
          if(d) {
            details+=' 响应类型：'+(['json','html','other'].includes(d.response_type)?d.response_type:'未知')+'；Cloudflare 标记：'+(d.cloudflare_header_present===true||d.cloudflare_server===true?'有':'未见')+'；挑战响应标记：'+(d.challenge_header===true?'有':'未见')+'。标记不能单独确认拒绝原因。';
          }
          setMessage((result.ok ? '' : '测试失败 ['+(/^[a-z_0-9]+$/.test(result.code||'')?result.code:'probe_failed')+']：')+result.message+details);
        }
      } catch (error) {
        // ApiError.status is numeric. Never display error.message/body/url or arbitrary response content.
        const status=error && Number.isInteger(error.status) && error.status>=0 && error.status<=599 ? error.status : null;
        const hints={401:'网页登录已过期，请重新登录。',403:'Dashboard 拒绝访问，请检查权限。',404:'插件测试路由不存在或未启用，请检查当前 Profile 并重启 Dashboard。',400:'可能仍在运行旧后端；请核对前后端版本并重启 Dashboard。',429:'Dashboard 请求过于频繁，请稍后再试。',502:'反向代理无法连接 Dashboard。',503:'Dashboard 暂不可用。',504:'代理等待 Dashboard 超时。'};
        if(status===0) setMessage('Dashboard 网络请求失败，未确认是否到达插件；检查网页与 Hermes 服务的连接。');
        else if(status!==null) setMessage('Dashboard 请求失败：HTTP '+status+'。'+(hints[status]||'检查 Dashboard 日志与前后端版本。')+'这不是已确认的馒头 HTTP 状态。');
        else setMessage('Dashboard 响应无法解析或请求失败（可能为非 JSON/旧版接口）。请核对前后端版本及代理。敏感详情未显示。');
      }
      finally {setBusy(false);}
    }
    if (!v) return h('div',{style:{padding:'24px'}},message,h('button',{style:btnStyle,onClick:reload},'重新读取'));
    return h('div',{style:{padding:'24px',maxWidth:'900px',margin:'auto'}},
      h('h1',{style:{fontSize:'24px',fontWeight:700}},'M-Team + qBittorrent'),
      h('p',null,'前端 v'+FRONTEND_VERSION+' · 后端 '+backendVersion+' · 业务构建 '+businessBuild),
      versionMismatch && h('div',{role:'alert',style:{border:'2px solid #d99232',padding:'14px',marginBottom:'16px'}},
        h('strong',null,'前后端版本不一致，已暂停保存和连接测试。'),
        h('p',null,'更新插件文件不会自动替换已经加载的 Python 后端。请重新加载提供本页 API 的 Dashboard 进程；不是只重启 Gateway。能独立管理 Dashboard 时无需重启整个容器。不要反复更新插件或改密钥。'),
        h('p',null,'若已重载仍不一致，请检查是否更新了另一 Profile、插件目录或服务实例。'),
        h('button',{type:'button',style:btnStyle,disabled:busy,onClick:reload},'重新检查后端版本')),
      h('p',null,'全部配置在本页完成，无需编辑文件、Compose 环境变量或手工安装 Skill。请仅在可信管理员页面填写凭据。'),
      h('p',{style:{opacity:.75}},'适配 Hermes v0.23.1 / Dashboard SDK 1.1.0。首版未完成真实账号端到端联调。'),
      h('form',{onSubmit:save},
        h('h2',null,'连接与凭据'),
        field('mteam_url','馒头 API 地址','API Key 会发往此地址，只接受 HTTPS。'),
        field('mteam_user_agent','馒头 User-Agent','默认 MTeamQBit/1.0。若站点要求可填写自己的兼容值；不会自动更换代理或绕过防护。'),
        field('qbit_url','qBittorrent WebUI 地址','同网络可用 http://qbittorrent:8080；否则用 NAS 内网 IP + 映射端口，不要填 localhost。'),
        ...[['mteam_api_key','馒头 API Key'],['qbit_username','qBittorrent 用户名'],['qbit_password','qBittorrent 密码']].map(([key,label])=>
          h('label',{key,style:{display:'block',marginBottom:'16px'}},h('div',{style:{fontWeight:600}},label+(present[key]?'（已设置，留空保留）':'（尚未设置）')),
            h('input',{type:'password',autoComplete:'new-password',style:inputStyle,disabled:busy,value:secrets[key],placeholder:'填写新值以新增或替换，不会回显已有值',onChange:e=>setSecrets(old=>({...old,[key]:e.target.value}))}))),
        h('p',null,'更换服务地址后，已有凭据会用于新地址。保存前核实目标可信；密钥写入 Hermes 管理的凭据存储，不写进插件普通配置。'),
        h('h2',null,'默认保存路径'),
        field('default_save_path','默认保存路径','填写 qBittorrent 容器内绝对路径（如 /downloads），不是 NAS 宿主机或 Hermes 路径。对话中可指定其他绝对路径，不指定时使用此默认值。'),
        h('p',null,'不限制根目录范围；请只授权可信用户添加任务。最终写入权限由 qBittorrent 的挂载和运行账户决定。'),
        h('div',{style:{marginTop:'20px'}},field('category','qBittorrent 类别','默认 hermes，提交时关闭自动 Torrent 管理以保留指定路径。')),
        h('label',{style:{display:'block',marginBottom:'20px'}},h('input',{type:'checkbox',checked:v.start_paused,disabled:busy,onChange:e=>change('start_paused',e.target.checked)}),' 添加后暂停（默认关闭；开启可用于首次验收）'),
        h('details',null,h('summary',null,'高级设置（接口差异与下载域名）'),
          h('label',{style:{display:'block',margin:'16px 0'}},'允许获取种子的域名（每行一个，精确匹配）',h('textarea',{style:inputStyle,rows:3,value:hosts,disabled:busy,onChange:e=>setHosts(e.target.value)})),
          field('search_path','搜索 API 路径','通常无需修改。'),field('detail_path','详情 API 路径','通常无需修改。'),field('download_path','下载令牌 API 路径','通常无需修改。'),
          h('label',null,'详情请求编码 ',h('select',{value:v.detail_encoding,disabled:busy,onChange:e=>change('detail_encoding',e.target.value)},h('option',{value:'form'},'form'),h('option',{value:'json'},'json'))),
          field('detail_origin','详情 origin（可留空）','站点需要时填写 DETAIL。'),field('search_mode','搜索模式','默认 normal。'),field('approval_ttl_seconds','准备请求有效期（秒）','60–86400 秒；默认 900。','number')),
        h('div',{style:{marginTop:'24px'}},h('button',{type:'submit',style:btnStyle,disabled:busy||versionMismatch},busy?'处理中…':'保存配置'),
          h('button',{type:'button',style:btnStyle,disabled:busy||versionMismatch,onClick:()=>test('mteam')},'测试馒头'),
          h('button',{type:'button',style:btnStyle,disabled:busy||versionMismatch,onClick:()=>test('qbit')},'测试 qBittorrent'))),
      h('p',null,'测试仅使用已保存的配置：馒头调用账户连接接口（不返回账户资料、不验证搜索）；qBit 登录并读取版本。不获取种子、不添加任务，也不能验证保存目录写权限。'),
      h('div',{role:'status','aria-live':'polite',style:{padding:'12px',border:'1px solid #8885',borderRadius:'6px',marginTop:'16px'}},message||'等待操作。'),
      h('p',{style:{opacity:.75}},'Skill 随插件启用自动注册为 mteam-qbit:mteam-qbit。只搜索不会下载；下载需用户明确选择资源和保存目录。')
    );
  }
  registry.register('mteam-qbit',Page);
})();
