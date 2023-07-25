import copy
import torch
torch.backends.cudnn.benchmark = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cuda.matmul.allow_tf32 = True
from rwkv.model import RWKV
from rwkv.utils import PIPELINE
import gc

class ModelUtils:

  model = None
  pipeline = None
  CHUNK_LEN = 100
  END_OF_TEXT = 0
  END_OF_LINE = 11
  DOUBLE_END_OF_LINE = 261
  CHN_PERIOD_END = 28329
  NEG_INF = -999999999
  AVOID_REPEAT = '，：？！'
  AVOID_REPEAT_TOKENS = []
  penalty_decay = 0.996
  
  def __init__(self, args):
    self.load_model(args.model, args.strategy)

  def load_model(self, model_path, strategy):
    self.model = RWKV(model=model_path, strategy=strategy)
    self.pipeline = PIPELINE(self.model, "rwkv_vocab_v20230424")
    for i in self.AVOID_REPEAT:
      dd = self.pipeline.encode(i)
      assert len(dd) == 1
      self.AVOID_REPEAT_TOKENS += dd

  def run_rnn(self, model_tokens, model_state, tokens):
    tokens = [int(x) for x in tokens]
    model_tokens += tokens
    while len(tokens) > 0:
      out, model_state = self.model.forward(tokens[:self.CHUNK_LEN], model_state)
      tokens = tokens[self.CHUNK_LEN:]
    if model_tokens[-1] in self.AVOID_REPEAT_TOKENS:
      out[model_tokens[-1]] = self.NEG_INF
    return out, model_tokens, model_state
  
  def get_reply(self, model_tokens, model_state, out, chat_param, occurrence={}):
    self.clear_cache()
    begin = len(model_tokens)
    out_last = begin
    for i in range(999):
      if i == 0 and chat_param['action_start_token']:
        out[chat_param['action_start_token']] = 10
      if chat_param['min_len'] >0 and i < chat_param['min_len']:
        out[self.CHN_PERIOD_END] = self.NEG_INF
        out[self.DOUBLE_END_OF_LINE] = self.NEG_INF
        out[self.END_OF_LINE] = self.NEG_INF    
      for n in occurrence:
        out[n] -= (chat_param['presence_penalty'] + occurrence[n] * chat_param['frequency_penalty'])
      token = self.pipeline.sample_logits(out, chat_param['temperature'], chat_param['top_p'])
      for o in occurrence:
        occurrence[o] *= self.penalty_decay
      occurrence[token] = 1 + (occurrence[token] if token in occurrence else 0)
      out, model_tokens, model_state = self.run_rnn(model_tokens, model_state, [token])
      out[self.END_OF_TEXT] = self.NEG_INF
      xxx = self.pipeline.decode(model_tokens[out_last:])
      if '\ufffd' not in xxx: # avoid utf-8 display issues
        out_last = begin + i + 1
      send_msg = self.pipeline.decode(model_tokens[begin:])
      if '\n\n' in send_msg:
        send_msg = send_msg.strip()
        break
    return send_msg, out, model_tokens, model_state
  
  def format_chat_param(self, top_p, temperature, presence_penalty, frequency_penalty, min_len=0, action_start_token=None, action_end_token=None):
    chat_param = {
      'top_p': top_p,
      'temperature': temperature,
      'presence_penalty': presence_penalty,
      'frequency_penalty': frequency_penalty,
      'min_len': min_len,
      'action_start_token': action_start_token,
      'action_end_token': action_end_token,
    }
    return chat_param
  
  def clear_cache(self):
    gc.collect()
    torch.cuda.empty_cache()
  