"""Conservative exit-only replay; never imported by a trading worker."""
def compare_exits(bars,entry,stop,target,side,slippage,cost_price=0):
    """Arm break-even after +1R only for the NEXT minute; stop wins ambiguity."""
    direction=1 if side=='long' else -1
    risk=abs(entry-stop)
    if risk<=0:raise ValueError('Invalid initial risk')
    def replay(protect):
        active=stop;armed=False
        for b in bars:
            o,h,l,c=(float(b[k]) for k in ('o','h','l','c'))
            if not 0<l<=min(o,c)<=max(o,c)<=h:raise ValueError('Invalid OHLC')
            hit_stop=l<=active if direction==1 else h>=active
            hit_take=h>=target if direction==1 else l<=target
            if hit_stop:
                raw=min(o,active) if direction==1 else max(o,active)
                return {'exit':raw-direction*slippage,'at':b['t'],'reason':'protected_stop' if armed else 'stop'}
            if hit_take:return {'exit':target-direction*slippage,'at':b['t'],'reason':'take'}
            favourable=h-entry if direction==1 else entry-l
            if protect and favourable>=risk and not armed:
                active=entry+direction*(cost_price+slippage);armed=True
        return None
    return {'baseline':replay(False),'break_even':replay(True)}
