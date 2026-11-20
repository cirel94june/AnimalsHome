/* Locate the login QR locally. Its decoded contents never leave this function. */
(() => {
  function render(image, output) {
    output.hidden = true;
    if (!window.jsQR || !image.naturalWidth || !image.naturalHeight) return false;
    const width=image.naturalWidth, height=image.naturalHeight;
    if (width>4096 || height>4096) return false;
    const source=document.createElement('canvas');
    // Exclude the page's left sidebar, which contains an App download QR.
    let offset=0, result=null;
    for (const fraction of [.2,.1]) {
      offset=Math.floor(width*fraction);
      source.width=width-offset*2;source.height=height;
      const ctx=source.getContext('2d',{willReadFrequently:true});
      ctx.drawImage(image,offset,0,source.width,height,0,0,source.width,height);
      const pixels=ctx.getImageData(0,0,source.width,height);
      result=window.jsQR(pixels.data,source.width,height,{inversionAttempts:'dontInvert'});
      if (result) break;
    }
    if (!result) return false;
    const corners=['topLeftCorner','topRightCorner','bottomLeftCorner','bottomRightCorner'].map(k=>result.location[k]);
    const x=Math.min(...corners.map(p=>p.x))+offset, y=Math.min(...corners.map(p=>p.y));
    const w=Math.max(...corners.map(p=>p.x))+offset-x, h=Math.max(...corners.map(p=>p.y))-y;
    if (![x,y,w,h].every(Number.isFinite) || w<20 || h<20) return false;
    const quiet=Math.ceil(Math.max(w,h)*.14), side=Math.ceil(Math.max(w,h))+quiet*2+2;
    output.width=side*3;output.height=side*3;
    const target=output.getContext('2d');target.fillStyle='#fff';target.fillRect(0,0,output.width,output.height);
    target.imageSmoothingEnabled=false;
    target.drawImage(image,Math.floor(x)-quiet,Math.floor(y)-quiet,side,side,0,0,output.width,output.height);
    output.hidden=false;
    return true;
  }
  window.XhsLoginQR={render};
})();
