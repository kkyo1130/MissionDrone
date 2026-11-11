/* Approximate 2D building flow for visualization only; no CFD validation.
 * A relaxed velocity potential enforces no-flow cells at building footprints.
 * A short downwind wake adds illustrative slowing and lateral disturbance.
 */
(function (root) {
  function build({buildings, west, south, width, depth, speed, direction, nx = 128, ny = 128}) {
    const lonScale = 111320 * Math.cos(south * Math.PI / 180);
    const dx = width / (nx - 1), dy = depth / (ny - 1);
    const theta = direction * Math.PI / 180;
    const u0 = -speed * Math.sin(theta), v0 = -speed * Math.cos(theta);
    const mask = new Uint8Array(nx * ny), potential = new Float64Array(nx * ny);
    const footprints = buildings.map(b => b.coordinates.map(([lon, lat]) => [(lon - west) * lonScale, (lat - south) * 111132]));
    function inside(x, y, polygon) {
      let hit = false;
      for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        const [a,b] = polygon[i], [c,d] = polygon[j];
        if ((b > y) !== (d > y) && x < (c-a)*(y-b)/(d-b)+a) hit = !hit;
      }
      return hit;
    }
    for (const polygon of footprints) {
      if (polygon.length < 3) continue;
      const xs = polygon.map(p=>p[0]), ys = polygon.map(p=>p[1]);
      for (let j=Math.max(0,Math.floor(Math.min(...ys)/dy)); j<=Math.min(ny-1,Math.ceil(Math.max(...ys)/dy)); j++) {
        for (let i=Math.max(0,Math.floor(Math.min(...xs)/dx)); i<=Math.min(nx-1,Math.ceil(Math.max(...xs)/dx)); i++) {
          if (inside(i*dx,j*dy,polygon)) mask[j*nx+i]=1;
        }
      }
    }
    for (let j=0;j<ny;j++) for(let i=0;i<nx;i++) potential[j*nx+i]=u0*i*dx+v0*j*dy;
    const wx=1/(dx*dx), wy=1/(dy*dy);
    for(let pass=0;pass<260;pass++) {
      for(let j=1;j<ny-1;j++) for(let i=1;i<nx-1;i++) {
        const k=j*nx+i;if(mask[k])continue;
        let sum=0,weight=0;
        for(const [offset,w] of [[-1,wx],[1,wx],[-nx,wy],[nx,wy]]) {
          if(!mask[k+offset]){sum+=potential[k+offset]*w;weight+=w;}
        }
        if(weight)potential[k]+=1.35*(sum/weight-potential[k]);
      }
    }
    const us=[],vs=[];
    let changed=0;
    // GRIB grid ordering: north to south.
    for(let j=ny-1;j>=0;j--) for(let i=0;i<nx;i++) {
      const k=j*nx+i;
      if(mask[k]){us.push(null);vs.push(null);continue;}
      const value=(x,y)=>mask[y*nx+x]?potential[k]:potential[y*nx+x];
      const left=Math.max(0,i-1),right=Math.min(nx-1,i+1);
      const bottom=Math.max(0,j-1),top=Math.min(ny-1,j+1);
      let u=(value(right,j)-value(left,j))/((right-left)*dx);
      let v=(value(i,top)-value(i,bottom))/((top-bottom)*dy);
      // Nearby upstream solid cells seed a synthetic lee-side wake.
      let wake=0;
      for(let step=2;step<=12;step+=2){
        const x=Math.round(i-u0/Math.max(speed,1e-9)*step);
        const y=Math.round(j-v0/Math.max(speed,1e-9)*step);
        if(x>=0&&x<nx&&y>=0&&y<ny&&mask[y*nx+x])wake=Math.max(wake,1-step/15);
      }
      const lateral=Math.sin(i*.65+j*.45)*wake*speed*.22;
      u=u*(1-wake*.55)-v0/Math.max(speed,1e-9)*lateral;
      v=v*(1-wake*.55)+u0/Math.max(speed,1e-9)*lateral;
      const magnitude=Math.hypot(u,v), cap=speed*2;
      if(magnitude>cap){u*=cap/magnitude;v*=cap/magnitude;}
      if(Math.hypot(u-u0,v-v0)>speed*.05)changed++;
      us.push(u);vs.push(v);
    }
    return {us,vs,mask,changed,nx,ny};
  }
  root.BuildingWind = {build};
})(typeof window !== 'undefined' ? window : globalThis);
