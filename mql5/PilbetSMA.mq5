//+------------------------------------------------------------------+
//|                                                  PilbetSMA.mq5   |
//| SMA 20/50 crossover for FXPesa MT5. Matches the Python engine:   |
//| evaluate two *completed* bars, trade the new bar's open.         |
//| DEMO FIRST. SMA crossovers have no reliable edge.                |
//+------------------------------------------------------------------+
#property copyright "pilbet"
#property version   "1.00"
#property description "XAUUSD SMA 20/50 crossover: 1% risk, 400-pip stop, 800-pip target"

#include <Trade/Trade.mqh>

input int    FastSMA        = 20;
input int    SlowSMA        = 50;
input double RiskPercent    = 1.0;
input int    StopLossPips   = 400;
input int    TakeProfitPips = 800;
input double MaxLots        = 1.0;
input long   MagicNumber    = 15092026;
input bool   AllowShort     = true;
input int    SlippagePoints = 20;

CTrade   g_trade;
int      g_fast_handle = INVALID_HANDLE;
int      g_slow_handle = INVALID_HANDLE;
datetime g_last_bar    = 0;

int OnInit()
{
   if(FastSMA < 2 || SlowSMA <= FastSMA)
   {
      Print("SlowSMA must be greater than FastSMA, FastSMA >= 2");
      return INIT_PARAMETERS_INCORRECT;
   }

   g_fast_handle = iMA(_Symbol, PERIOD_CURRENT, FastSMA, 0, MODE_SMA, PRICE_CLOSE);
   g_slow_handle = iMA(_Symbol, PERIOD_CURRENT, SlowSMA, 0, MODE_SMA, PRICE_CLOSE);
   if(g_fast_handle == INVALID_HANDLE || g_slow_handle == INVALID_HANDLE)
   {
      Print("Failed to create SMA handles: ", GetLastError());
      return INIT_FAILED;
   }

   g_trade.SetExpertMagicNumber(MagicNumber);
   g_trade.SetDeviationInPoints(SlippagePoints);
   g_trade.SetAsyncMode(false);
   _set_filling();

   Print("PilbetSMA ready on ", _Symbol, " ", EnumToString(_Period),
         " sl=", StopLossPips, "tp=", TakeProfitPips, " risk=", RiskPercent, "%");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_fast_handle != INVALID_HANDLE)
      IndicatorRelease(g_fast_handle);
   if(g_slow_handle != INVALID_HANDLE)
      IndicatorRelease(g_slow_handle);
}

void OnTick()
{
   if(!_is_new_bar())
      return;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      Print("AutoTrading disabled — enable the AutoTrading button");
      return;
   }

   double fast[], slow[];
   ArraySetAsSeries(fast, true);
   ArraySetAsSeries(slow, true);
   if(CopyBuffer(g_fast_handle, 0, 0, 3, fast) < 3)
      return;
   if(CopyBuffer(g_slow_handle, 0, 0, 3, slow) < 3)
      return;

   // bar 1 = last completed, bar 2 = previous completed (never bar 0)
   const bool prev_bull = fast[2] > slow[2];
   const bool last_bull = fast[1] > slow[1];
   const bool golden = (!prev_bull && last_bull);
   const bool death  = (prev_bull && !last_bull);
   if(!golden && !death)
      return;

   const long pos_type = _our_position_type();
   if(golden)
   {
      if(pos_type == POSITION_TYPE_BUY)
         return;
      if(pos_type == POSITION_TYPE_SELL)
         _close_our_position();
      _open(ORDER_TYPE_BUY);
      return;
   }

   if(pos_type == POSITION_TYPE_SELL)
      return;
   if(pos_type == POSITION_TYPE_BUY)
      _close_our_position();
   if(AllowShort)
      _open(ORDER_TYPE_SELL);
}

bool _is_new_bar()
{
   const datetime bar_time = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar_time == 0)
      return false;
   if(bar_time == g_last_bar)
      return false;
   const bool first = (g_last_bar == 0);
   g_last_bar = bar_time;
   return !first;
}

long _our_position_type()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      return PositionGetInteger(POSITION_TYPE);
   }
   return -1;
}

void _close_our_position()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      if(!g_trade.PositionClose(ticket))
         Print("Close failed ticket=", ticket, " ret=", g_trade.ResultRetcode(),
               " ", g_trade.ResultRetcodeDescription());
   }
}

void _open(const ENUM_ORDER_TYPE type)
{
   const double pip = _pip_size();
   const double sl_dist = StopLossPips * pip;
   const double tp_dist = TakeProfitPips * pip;
   const bool is_buy = (type == ORDER_TYPE_BUY);
   const double price = is_buy
      ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
      : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   const double lots = _lots_for_stop(sl_dist);
   if(lots <= 0)
   {
      Print("Lot size rejected (risk too small for min volume)");
      return;
   }

   double sl, tp;
   if(is_buy)
   {
      sl = price - sl_dist;
      tp = price + tp_dist;
   }
   else
   {
      sl = price + sl_dist;
      tp = price - tp_dist;
   }
   sl = _norm_price(sl);
   tp = _norm_price(tp);

   const string comment = "pilbet sma";
   const bool ok = is_buy
      ? g_trade.Buy(lots, _Symbol, price, sl, tp, comment)
      : g_trade.Sell(lots, _Symbol, price, sl, tp, comment);
   if(!ok)
      Print("Order failed ret=", g_trade.ResultRetcode(), " ",
            g_trade.ResultRetcodeDescription());
   else
      Print("Opened ", (is_buy ? "BUY" : "SELL"), " lots=", lots,
            " price=", price, " sl=", sl, " tp=", tp);
}

double _lots_for_stop(const double sl_distance)
{
   const double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   const double risk = equity * RiskPercent / 100.0;
   const double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   const double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tick_size <= 0 || sl_distance <= 0 || tick_value <= 0 || risk <= 0)
      return 0;

   const double loss_per_lot = (sl_distance / tick_size) * tick_value;
   double lots = risk / loss_per_lot;
   const double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   const double vmax_sym = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   const double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   const double vmax = MathMin(MaxLots, vmax_sym);
   if(step > 0)
      lots = MathFloor(lots / step + 1e-12) * step;
   if(lots < vmin)
      return 0;
   if(lots > vmax)
      lots = vmax;
   return NormalizeDouble(lots, 2);
}

double _pip_size()
{
   const int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   const double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(digits == 3 || digits == 5)
      return point * 10.0;
   return point;
}

double _norm_price(const double price)
{
   const int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   return NormalizeDouble(price, digits);
}

void _set_filling()
{
   const long filling = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      g_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else if((filling & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      g_trade.SetTypeFilling(ORDER_FILLING_FOK);
}
