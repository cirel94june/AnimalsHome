/* Locate the login QR locally. Its decoded contents never leave this function. */
(() => {
  function render(image, output) {
    output.hidden = true;
    if (!window.jsQR || !image.naturalWidth || !image.naturalHeight) return false;
    const width=image.naturalWidth, height=image.naturalHeight;
    if (width>4096 || height>4096) return false;
    const source=document.createElement('canvas');
    // Check the login dialog before the wider fallback. The left sidebar's
    // App download QR is excluded; no QR payload needs decoding.
    const regions=width>=1000 ? [[.2,.3,.3,.4],[.1,.15,.55,.65]] : [[.05,.15,.5,.7],[.1,0,.8,1]];
    let offset=0, top=0, result=null;
    for (const [left,y,w,h] of regions) {
      offset=Math.floor(width*left);top=Math.floor(height*y);
      source.width=Math.floor(width*w);source.height=Math.floor(height*h);
      const ctx=source.getContext('2d',{willReadFrequently:true});
      ctx.imageSmoothingEnabled=false;
      ctx.drawImage(image,offset,top,source.width,source.height,0,0,source.width,source.height);
      const pixels=ctx.getImageData(0,0,source.width,source.height);
      result=window.jsQR(pixels.data,source.width,source.height,{inversionAttempts:'dontInvert'}) ||
        window.jsQR.locate?.(pixels.data,source.width,source.height);
      if (result) break;
    }
    if (!result) return false;
    const corners=['topLeftCorner','topRightCorner','bottomLeftCorner','bottomRightCorner'].map(k=>result.location[k]);
    const x=Math.min(...corners.map(p=>p.x))+offset, y=Math.min(...corners.map(p=>p.y))+top;
    const w=Math.max(...corners.map(p=>p.x))+offset-x, h=Math.max(...corners.map(p=>p.y))+top-y;
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
